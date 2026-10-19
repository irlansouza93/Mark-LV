"""Classifying a dropped Live session — what main.py's reconnect loop decides on.

Seen in the real app on 2026-10-17: a network drop mid-session reached the loop
as an ExceptionGroup ("unhandled errors in a TaskGroup (1 sub-exception)"). The
resumption-rejected branch tested `"handle" in str(e).lower()`, which matches
"unHANDLEd", so the handle was thrown away and the loop reconnected with no
backoff; the network test read the same group text and never saw the
ConnectionResetError / APIError 1006 inside.
"""
import asyncio
import threading

from core import live_session as ls


class _FakeAPIError(Exception):
    """Stands in for google.genai.errors.APIError (no SDK import in tests)."""


def _group(*leaves):
    # The shape TaskGroup produces, nested one level like a real session.
    return BaseExceptionGroup("unhandled errors in a TaskGroup", [
        ExceptionGroup("unhandled errors in a TaskGroup", list(leaves))])


def test_leaves_are_flattened():
    a, b = ConnectionResetError(64, "x"), ValueError("y")
    assert ls.leaf_errors(_group(a, b)) == [a, b]
    assert ls.leaf_errors(a) == [a]


def test_group_text_does_not_look_like_a_rejected_handle():
    e = _group(ConnectionResetError(10054, "[WinError 64] network name gone"))
    assert not ls.is_resume_rejected(e)


def test_network_leaves_are_seen_through_the_group():
    assert ls.is_network_error(_group(ConnectionResetError(10054, "[WinError 64]")))
    assert ls.is_network_error(_group(_FakeAPIError("1006 None. abnormal closure [internal]")))
    assert ls.is_network_error(_group(TimeoutError()))
    assert ls.is_network_error(OSError("getaddrinfo failed"))


def test_ordinary_errors_are_not_network():
    assert not ls.is_network_error(_group(ValueError("bad value")))
    assert not ls.is_network_error(_FakeAPIError("1007 API key not valid"))


def test_a_real_rejection_is_still_recognised():
    assert ls.is_resume_rejected(_FakeAPIError("1007 invalid session resumption handle"))
    assert ls.is_resume_rejected(_group(_FakeAPIError("NOT_FOUND: session")))
    assert ls.is_resume_rejected(_FakeAPIError("INVALID_ARGUMENT: handle expired"))


def test_network_drop_wins_over_rejection_words():
    # A dropped socket says nothing about the handle: keep it and resume.
    e = _group(_FakeAPIError("1006 abnormal closure"), _FakeAPIError("NOT_FOUND"))
    assert not ls.is_resume_rejected(e)


def test_error_text_keeps_the_top_message_and_adds_leaves():
    e = _group(_FakeAPIError("429 RESOURCE_EXHAUSTED"))
    txt = ls.error_text(e)
    assert "unhandled errors in a TaskGroup" in txt
    assert "RESOURCE_EXHAUSTED" in txt
    assert ls.error_text(ValueError("plain")) == "plain"


# ── QueueFull from the mic thread ────────────────────────────────────────────

def _flood(target, n):
    """Schedule `n` puts from another thread, like the sounddevice callback."""
    loop = asyncio.new_event_loop()
    q = asyncio.Queue(maxsize=3)
    errors = []
    loop.set_exception_handler(lambda _l, ctx: errors.append(ctx.get("exception")))

    def mic():
        for i in range(n):
            loop.call_soon_threadsafe(target(q), {"data": bytes([i])})
    t = threading.Thread(target=mic)
    t.start()
    t.join()
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.close()
    return q, errors


def test_bare_put_nowait_raises_queuefull_in_the_loop():
    _q, errors = _flood(lambda q: q.put_nowait, 10)
    assert any(isinstance(e, asyncio.QueueFull) for e in errors)


def test_put_or_drop_never_raises_and_keeps_the_first_blocks():
    q, errors = _flood(lambda q: (lambda item: ls.put_or_drop(q, item)), 10)
    assert errors == []
    assert q.qsize() == 3
    assert q.get_nowait()["data"] == bytes([0])
