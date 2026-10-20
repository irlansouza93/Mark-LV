"""Wait safety net for the voice session: knows how long the user has been waiting
for the first sound of an answer, and tells the caller when to show "thinking",
speak a short phrase, or give up.

This module is the pure part: no audio, no Qt, no network, no clock of its own
(``clock`` is injected). ``main.py`` feeds it events from the Live receive loop and
calls ``tick()`` every ~100 ms; it only ever answers with action names and a record
to log, so the whole state machine is tested with a fake clock.

Why the anchor is the LAST chunk of ``input_transcription`` (spec 002, research R1):
the session uses server-side voice detection, so the client never gets an "end of
speech" event. What does arrive while the user talks are transcription chunks; the
last one is the closest signal that the server heard speech that has now stopped.
Noise the server does not transcribe never opens an episode, and a late transcript
only makes the net fire later, never earlier. A typed request opens the episode when
it is sent.

Why a quiet model is not an error (R2): proactive audio is ON, so for speech that
was not addressed to the assistant the server may close the turn with no audio at
all. That is ``turn_complete`` without audio -> outcome ``no_reply``, no phrase. A
phrase or give-up is only ever emitted while the episode is still open.

Measuring never switches off: with ``enabled = False`` the watchdog emits no actions
but still returns records, so the usage report keeps its numbers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

_IDLE, _WAITING, _EXCHANGE = "idle", "waiting", "exchange"


@dataclass(frozen=True)
class Limits:
    """Wait limits in ms, counted from the anchor (see the module docstring)."""
    enabled: bool = True
    thinking_ms: int = 1500
    phrase_ms: int = 4000
    give_up_ms: int = 20000

    @classmethod
    def from_config(cls, cfg: dict) -> "Limits":
        """Build from ``config_manager.get_wait_cue()`` (already validated there)."""
        cfg = cfg if isinstance(cfg, dict) else {}
        d = cls()
        return cls(
            enabled=bool(cfg.get("enabled", d.enabled)),
            thinking_ms=int(cfg.get("thinking_ms", d.thinking_ms)),
            phrase_ms=int(cfg.get("phrase_ms", d.phrase_ms)),
            give_up_ms=int(cfg.get("give_up_ms", d.give_up_ms)),
        )


def _safe(default=None):
    """Event methods run on the asyncio loop that also moves audio: they must never
    raise, whatever the clock or the caller does. A bug here is still recorded
    (constitution VII) — errlog.record_failure only queues a dict and never raises."""
    def deco(fn):
        def wrapper(self, *a, **kw):
            try:
                return fn(self, *a, **kw)
            except Exception as e:
                try:
                    from core import errlog
                    errlog.record_failure("voice", f"wait_cue.{fn.__name__}", exc=e)
                except Exception:
                    pass        # the logger itself is unavailable; nothing else can report it
                return default() if callable(default) else default
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return deco


class WaitWatchdog:
    """One episode = one request of the user until the first sound of the answer.

    States: IDLE (nothing pending), WAITING (episode open), EXCHANGE (the model is
    answering; a late input transcript must not open a new episode until
    ``turn_complete``). Every event is O(1) and does no I/O.
    """

    def __init__(self, limits: Limits, clock: Callable[[], float] = time.monotonic):
        self._lim = limits
        self._clock = clock
        self._state = _IDLE
        self._reset_episode()

    # ── episode bookkeeping ──────────────────────────────────────────────────
    def _reset_episode(self) -> None:
        self._source = "voice"
        self._anchor = 0.0
        self._thinking = False
        self._phrase: str | None = None          # None | "pending" | "voice" | "tone"
        self._tool_active = 0
        self._tool_seen = False
        self._last_tool_end: float | None = None
        self._gave_up = False

    def _open(self, source: str, now: float) -> None:
        self._reset_episode()
        self._source = source
        self._anchor = now
        self._state = _WAITING

    def _record(self, outcome: str, latency_ms=None, cancel_reason=None) -> dict:
        lim = self._lim
        return {
            "source": self._source,
            "outcome": outcome,
            "cancel_reason": cancel_reason,
            "latency_ms": latency_ms,
            "tool": self._tool_seen,
            "thinking": self._thinking,
            "phrase": self._phrase if self._phrase in ("voice", "tone") else None,
            "gave_up": self._gave_up,
            "enabled": lim.enabled,
            "limits": {"thinking_ms": lim.thinking_ms, "phrase_ms": lim.phrase_ms,
                       "give_up_ms": lim.give_up_ms},
        }

    # ── events ───────────────────────────────────────────────────────────────
    @_safe()
    def user_speech(self) -> None:
        """A chunk of ``input_transcription``: opens the episode or pushes its anchor."""
        now = self._clock()
        if self._state == _IDLE:
            self._open("voice", now)
        elif self._state == _WAITING:
            self._anchor = now
        # EXCHANGE: a late transcript of the request already being answered

    @_safe()
    def user_text(self) -> None:
        """A typed request was really sent to the model."""
        now = self._clock()
        if self._state == _IDLE:
            self._open("text", now)
        elif self._state == _WAITING:
            self._anchor = now
        # EXCHANGE: audio of the previous answer may still be arriving; opening an
        # episode now would close it on that audio with a false latency.

    @_safe()
    def model_audio(self) -> dict | None:
        """First audio of the model closes the episode. Without an open episode (the
        app itself sent text, e.g. proactive mode) it is a no-op: not an exchange."""
        if self._state != _WAITING:
            return None
        rec = self._record("audio", latency_ms=round((self._clock() - self._anchor) * 1000))
        self._state = _EXCHANGE
        return rec

    @_safe()
    def tool_started(self) -> None:
        if self._state == _WAITING:
            self._tool_active += 1
            self._tool_seen = True

    @_safe()
    def tool_finished(self) -> None:
        if self._state == _WAITING:
            self._tool_active = max(0, self._tool_active - 1)
            self._last_tool_end = self._clock()

    @_safe()
    def turn_complete(self) -> dict | None:
        """Turn over: no audio so far -> ``no_reply``; after audio -> back to IDLE."""
        if self._state == _WAITING:
            rec = self._record("no_reply")
            self._state = _IDLE
            return rec
        self._state = _IDLE
        return None

    @_safe()
    def cancel(self, reason: str) -> dict | None:
        """interrupt | muted | asleep | session | disabled."""
        if self._state == _WAITING:
            rec = self._record("cancelled", cancel_reason=reason)
            self._state = _IDLE
            return rec
        self._state = _IDLE
        return None

    @_safe()
    def phrase_played(self, how: str) -> None:
        """What the caller actually played for the "phrase" action: ``voice`` or ``tone``."""
        if self._state == _WAITING and how in ("voice", "tone"):
            self._phrase = how

    # ── tick (every ~100 ms) ─────────────────────────────────────────────────
    @_safe(default=lambda: ([], None))
    def tick(self) -> tuple[list[str], dict | None]:
        """Actions due now, in order, each at most once per episode, plus the record
        when ``give_up`` closes the episode."""
        if self._state != _WAITING or not self._lim.enabled:
            return [], None
        lim = self._lim
        now = self._clock()
        waited_ms = (now - self._anchor) * 1000
        actions: list[str] = []
        if not self._thinking and waited_ms >= lim.thinking_ms:
            self._thinking = True
            actions.append("thinking")
        if self._phrase is None and waited_ms >= lim.phrase_ms:
            self._phrase = "pending"
            actions.append("phrase")
        if self._tool_active == 0:
            since = max(self._anchor, self._last_tool_end or 0.0)
            if (now - since) * 1000 >= lim.give_up_ms:
                self._gave_up = True
                actions.append("give_up")
                rec = self._record("gave_up")
                self._state = _IDLE
                return actions, rec
        return actions, None

    # ── state for the caller ─────────────────────────────────────────────────
    @property
    def waiting(self) -> bool:
        return self._state == _WAITING

    @property
    def thinking_shown(self) -> bool:
        return self._thinking
