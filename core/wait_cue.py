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

The second half of the module is the spoken part (R3/R4): the two short phrases
("hold" and "giveup") live as WAVs in ``config/voice_cache`` next to the picker's
samples, generated once per voice and language by a daemon thread, and played by
``main.py`` through the same output queue as the model's voice. Playing never
calls the API: with no cached file it plays a soft tone, so the wait net still
makes a sound when the network, the key or the quota is the very reason for the wait.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

_IDLE, _WAITING, _EXCHANGE = "idle", "waiting", "exchange"

# An episode that closes in under this many ms did not wait at all: the transcript of
# the request reached us together with the first audio of the answer (the server sends
# input_transcription late, sometimes after the answer has started). Real test of
# 2026-10-19: 1 of 8 requests registered 1 ms. Counting it as a latency would drag the
# percentiles (and the suggested limits) down, so it closes as ``cancelled`` instead.
LATE_TRANSCRIPT_MS = 150


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
        self._thinking = False                   # "thinking" is currently on screen
        self._thinking_ever = False              # it was shown at some point (for the record)
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
            "thinking": self._thinking_ever,
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
            # The user spoke again: the caller puts the HUD back to listening, and
            # "thinking" may show again from the new anchor (the phrase stays once).
            self._thinking = False
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
        latency_ms = round((self._clock() - self._anchor) * 1000)
        if latency_ms < LATE_TRANSCRIPT_MS:
            rec = self._record("cancelled", cancel_reason="late_transcript")
        else:
            rec = self._record("audio", latency_ms=latency_ms)
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
            self._thinking = self._thinking_ever = True
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
    def source(self) -> str:
        """How the open (or last) episode started: ``voice`` or ``text``."""
        return self._source

    @property
    def waiting(self) -> bool:
        return self._state == _WAITING

    @property
    def thinking_shown(self) -> bool:
        return self._thinking


# ── spoken phrases (spec 002 US2, research R3/R4/R4b) ────────────────────────
RATE = 24000        # = RECEIVE_SAMPLE_RATE in main.py: the playback stream's rate

# Same seven languages as the voice picker (voice_preview._PHRASES). BCP-47 goes in the
# TTS speech config: without it the accent drifts to English/Italian (R4b, measured).
BCP47 = {"pt": "pt-BR", "en": "en-US", "es": "es-US", "fr": "fr-FR",
         "de": "de-DE", "it": "it-IT", "tr": "tr-TR"}

# Several variants per kind so the same sentence is not heard on every wait. Every one has
# at least five words: shorter ones came out in the wrong language ("Um instante." ->
# "Ohm's inch", 1.1 s) while "Só um momento, já vejo isso." is clean pt-BR in 2.2 s (R4b).
# "hold" must end before a typical answer starts; "giveup" is the last thing the user
# hears from a session that has gone quiet.
TEXTS: dict[str, dict[str, list[str]]] = {
    "hold": {
        "pt": ["Só um momento, já vejo isso.",
               "Um instante, estou verificando para você.",
               "Deixa eu conferir isso rapidinho.",
               "Estou cuidando disso, só um segundo.",
               "Já estou vendo isso, um momento."],
        "en": ["Just a moment, I'm checking that now.",
               "One second, let me look into that.",
               "Hold on a moment, I'm on it."],
        "es": ["Un momento, ya lo estoy revisando.",
               "Dame un segundo, lo voy a comprobar.",
               "Espera un momento, me ocupo de eso."],
        "fr": ["Un instant, je vérifie cela tout de suite.",
               "Une seconde, je regarde ça pour vous.",
               "Laissez-moi vérifier cela un instant."],
        "de": ["Einen Moment bitte, ich prüfe das kurz.",
               "Ich schaue mir das gleich an, einen Augenblick.",
               "Gib mir bitte kurz einen Moment dafür."],
        "it": ["Un momento, sto controllando subito.",
               "Dammi un secondo, lo verifico adesso.",
               "Solo un attimo, me ne occupo io."],
        "tr": ["Bir saniye bekle, hemen kontrol ediyorum.",
               "Bir dakika, buna hemen bakıyorum.",
               "Hemen ilgileniyorum, bir an lütfen bekle."],
    },
    "giveup": {
        "pt": ["Desculpe, não consegui responder. Pode repetir?",
               "Não recebi a resposta a tempo. Pode falar de novo?",
               "Perdi o fio aqui. Pode repetir o pedido?"],
        "en": ["Sorry, I couldn't answer that. Could you repeat?",
               "I didn't get a reply in time. Please say that again."],
        "es": ["Lo siento, no pude responder. ¿Puedes repetirlo?",
               "No recibí la respuesta a tiempo. ¿Lo repites?"],
        "fr": ["Désolé, je n'ai pas pu répondre. Pouvez-vous répéter ?",
               "Je n'ai pas reçu la réponse à temps. Pouvez-vous répéter ?"],
        "de": ["Entschuldigung, ich konnte nicht antworten. Kannst du das wiederholen?",
               "Die Antwort kam nicht rechtzeitig. Kannst du es noch einmal sagen?"],
        "it": ["Scusa, non sono riuscito a rispondere. Puoi ripetere?",
               "Non ho ricevuto la risposta in tempo. Puoi ripetere?"],
        "tr": ["Üzgünüm, cevap veremedim. Tekrar eder misin?",
               "Yanıt zamanında gelmedi. Lütfen tekrar söyler misin?"],
    },
}

# Spoken seconds allowed after trimming the silence at both ends (R4b): under 0.6 s the
# TTS clipped the sentence; over the maximum it padded it with silence (10.5 s measured
# for a 2 s phrase) or read the instruction aloud.
_DURATION_S = {"hold": (0.6, 4.5), "giveup": (0.6, 6.0)}
_SILENCE = 0.02                 # samples under 2% of the absolute peak are silence

_last_variant: dict[str, int] = {}      # kind -> variant played last (pcm_for avoids repeating it)


def _variants(kind: str, lang: str) -> list[str]:
    return TEXTS[kind].get(lang) or TEXTS[kind]["en"]


def phrase_path(kind: str, index: int, voice: str, lang: str) -> Path:
    from core import voice_preview          # CACHE_DIR is read at call time (tests swap it)
    # "v2": the version-1 files ("wait_<kind>_<voice>_<lang>.wav") had the wrong accent
    # and are never read again; they can be deleted by hand.
    return voice_preview.CACHE_DIR / f"wait_v2_{kind}_{index}_{voice}_{lang}.wav"


def _to_rate(pcm: np.ndarray, rate: int) -> np.ndarray:
    """Linear interpolation to 24 kHz: a spoken phrase does not need better, and
    numpy is already a dependency (no resampler library for two sentences)."""
    if rate == RATE or pcm.size == 0:
        return pcm.astype(np.int16, copy=False)
    n = int(round(pcm.size * RATE / rate))
    x = np.linspace(0.0, pcm.size - 1, num=max(n, 1))
    return np.interp(x, np.arange(pcm.size), pcm.astype(np.float32)).astype(np.int16)


def load_phrase(kind: str, index: int, voice: str, lang: str) -> np.ndarray | None:
    """Cached phrase as int16 @ 24 kHz, or None (no file, or one that cannot be read)."""
    from core import voice_preview
    path = phrase_path(kind, index, voice, lang)
    try:
        if not path.exists():
            return None
        pcm, rate = voice_preview._read_wav(path)
        return _to_rate(pcm, rate) if pcm.size else None
    except Exception:
        return None            # a corrupt file is the same as no file: fall back to the tone


def tone() -> np.ndarray:
    """Two soft sine beeps, 880 -> 660 Hz, ~300 ms in all (120 ms each, 60 ms gap),
    faded in and out so there is no click, peak 0.25 of full scale. Pure numpy:
    it cannot fail, which is the point of a fallback."""
    def beep(freq: float, ms: int) -> np.ndarray:
        n = RATE * ms // 1000
        t = np.arange(n) / RATE
        fade = np.minimum(1.0, np.minimum(np.arange(n), np.arange(n)[::-1]) / (RATE * 0.015))
        return 0.25 * np.sin(2 * np.pi * freq * t) * fade
    gap = np.zeros(RATE * 60 // 1000)
    wave_ = np.concatenate([beep(880.0, 120), gap, beep(660.0, 120)])
    return (wave_ * 32767).astype(np.int16)


def pcm_for(kind: str, voice: str, lang: str,
            rng: random.Random | None = None) -> tuple[np.ndarray, str]:
    """What to play for ``kind``: ``(pcm, "voice")`` from the cache, else ``(tone(), "tone")``.

    Draws among the variants that are cached, never the one played last for this
    ``kind`` (with a single cached variant it repeats). Never touches the network and
    never raises. ``rng`` is injectable for tests."""
    try:
        pick = rng or random
        cached = [i for i in range(len(_variants(kind, lang)))
                  if phrase_path(kind, i, voice, lang).exists()]
        last = _last_variant.get(kind)
        pool = [i for i in cached if i != last] or cached
        while pool:
            i = pick.choice(pool)
            pcm = load_phrase(kind, i, voice, lang)
            if pcm is not None:
                _last_variant[kind] = i
                return pcm, "voice"
            pool.remove(i)         # unreadable file: try another variant
    except Exception:
        pass
    return tone(), "tone"


def _trim_and_check(kind: str, pcm: np.ndarray, rate: int) -> np.ndarray:
    """Cut the silence at both ends and check the spoken length (R4b); raise
    ``RuntimeError("phrase audio <dur>s outside <min>-<max>s")`` when it is wrong."""
    lo, hi = _DURATION_S[kind]
    mag = np.abs(pcm.astype(np.int32))
    peak = int(mag.max()) if mag.size else 0
    loud = np.nonzero(mag >= max(1, peak * _SILENCE))[0] if peak else np.array([], dtype=int)
    trimmed = pcm[loud[0]:loud[-1] + 1] if loud.size else pcm[:0]
    dur = len(trimmed) / rate
    if not lo <= dur <= hi:
        raise RuntimeError(f"phrase audio {dur:.1f}s outside {lo}-{hi}s")
    return trimmed


def prefetch(voice: str, lang: str, on_error: Callable[[str, str], None] | None = None,
             synth: Callable | None = None) -> threading.Thread | None:
    """Generate the phrase variants that are not cached yet, off the calling thread.

    Returns the daemon thread, or None when everything is already on disk (the
    common case after the first run). The audio is trimmed and its length checked
    before it is written (a bad take is not cached); a failure of one variant goes to
    ``on_error(kind, message)``, leaves no file behind (``_write_wav`` is atomic) and
    does not stop the other variants, so the next connection simply tries again.
    ``synth`` is injectable for tests; the default is the picker's TTS ladder with the
    bare text and the language code (no style line: see ``voice_preview._synthesise``).
    """
    # An existing but unreadable or empty file counts as missing: otherwise a bad
    # cache would mean the tone forever, with nothing ever regenerating it.
    missing = [(kind, i, text) for kind in TEXTS
               for i, text in enumerate(_variants(kind, lang))
               if load_phrase(kind, i, voice, lang) is None]
    if not missing:
        return None

    def work() -> None:
        from core import voice_preview
        fn = synth or voice_preview._synthesise
        code = BCP47.get(lang, "en-US")
        for kind, i, text in missing:
            try:
                pcm, rate = fn(voice, lang, text=text, style=None, language_code=code)
                if pcm is None or len(pcm) == 0:
                    raise RuntimeError("TTS returned no audio")
                pcm = _trim_and_check(kind, pcm, rate)
                voice_preview._write_wav(phrase_path(kind, i, voice, lang), _to_rate(pcm, rate), RATE)
            except Exception as e:
                if on_error:
                    try:
                        on_error(kind, str(e)[:160])
                    except Exception:
                        pass

    th = threading.Thread(target=work, daemon=True, name="wait-cue-prefetch")
    th.start()
    return th
