"""
Small, pure helpers for the Live session loop in main.py.

Kept out of main.py so they can be tested without PyQt, sounddevice or the
Gemini SDK — the reconnect decisions are exactly the code that only runs when
something has already gone wrong, which is the worst place to find a bug live.

Why the error helpers exist
---------------------------
Everything the session runs lives in one `asyncio.TaskGroup`. When a task dies
mid-session the loop does not get the error — it gets a (Base)ExceptionGroup
whose text is "unhandled errors in a TaskGroup (1 sub-exception)". Matching
words against `str(group)` therefore sees none of the real failure, and worse,
"unhandled" contains "handle": a network drop (ConnectionResetError / WinError
64, or APIError 1006 "abnormal closure", both seen on 2026-10-17) was read as
"the server rejected the resumption handle", which threw the handle away and
reconnected with no backoff — losing the conversation the handle exists to keep.
"""
from __future__ import annotations

import asyncio
import re
import socket


def leaf_errors(exc: BaseException) -> list[BaseException]:
    """The real failures inside `exc`, with every exception group unwrapped."""
    if isinstance(exc, BaseExceptionGroup):
        out: list[BaseException] = []
        for sub in exc.exceptions:
            out.extend(leaf_errors(sub))
        return out
    return [exc]


def _leaf_text(exc: BaseException) -> str:
    return " | ".join(f"{type(x).__name__}: {x}" for x in leaf_errors(exc))


def error_text(exc: BaseException) -> str:
    """`str(exc)` plus, for a group, the type and text of every leaf.

    A superset of the old `str(e)`, so every substring test that matched before
    still matches — and the ones meant for a mid-session failure (quota,
    invalid key) finally can.
    """
    if isinstance(exc, BaseExceptionGroup):
        return f"{exc} | {_leaf_text(exc)}"
    return str(exc)


# Words a dropped connection leaves in the text when the type alone does not
# say so: APIError carries the websocket close code (1006 = abnormal closure,
# no close frame), and the websockets library reports a dead keepalive.
_NET_WORDS = (
    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
    "ConnectionRefusedError", "OSError", "Cannot connect",
    "1006", "abnormal closure", "no close frame", "keepalive ping timeout",
)
_NET_TYPES = (ConnectionError, TimeoutError, socket.gaierror)


def is_network_error(exc: BaseException) -> bool:
    """True when any leaf of `exc` is a dropped or unreachable connection."""
    for leaf in leaf_errors(exc):
        if isinstance(leaf, _NET_TYPES):
            return True
        if "ConnectionClosed" in type(leaf).__name__:
            return True
        text = f"{type(leaf).__name__}: {leaf}"
        if any(w in text for w in _NET_WORDS):
            return True
    return False


# `\bhandle\b`, not "handle": "unhandled" must never read as a rejected handle.
_REJECT_RE = re.compile(r"resum|\bhandle\b|INVALID_ARGUMENT|NOT_FOUND", re.IGNORECASE)


def is_resume_rejected(exc: BaseException) -> bool:
    """True when the server refused the resumption handle we replayed.

    A network drop never counts, whatever else the group holds: a dead socket
    says nothing about the handle, and keeping it is what lets the next connect
    pick the conversation up where it stopped.
    """
    if is_network_error(exc):
        return False
    return bool(_REJECT_RE.search(_leaf_text(exc)))


def put_or_drop(q: asyncio.Queue, item) -> None:
    """`q.put_nowait(item)`, dropping the item when the queue is full.

    For the microphone callback, which schedules this from the audio thread.
    A bare `put_nowait` raised QueueFull inside the event loop during a network
    drop: the sender sits in `send_realtime_input` on a dead socket while the
    mic keeps producing a 64 ms block (1024 frames at 16 kHz), so the 200-slot
    queue is full after ~12.8 s — well before the keepalive gives up. The
    oldest blocks are kept, as `_relay_phone_audio` already does for the phone.
    """
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
