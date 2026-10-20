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

The phrases come from the Live model itself, in the voice of the conversation (R4c); the
TTS ladder is the reserve and the tone the last resort. See ``prefetch``.
"""
from __future__ import annotations

import asyncio
import difflib
import random
import re
import threading
import time
import unicodedata
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
        self._echo_suspect = False               # our own phrase came back through the mic (R3b)

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
            "echo_suspect": self._echo_suspect,
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

    @_safe()
    def phrase_heard_back(self) -> None:
        """The server transcribed our own wait phrase as if the user had said it: the
        record of the open episode says so (diagnostic only, no text kept)."""
        if self._state == _WAITING:
            self._echo_suspect = True

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
        "pt": ["Só um momento, por favor.",
               "Um instante, já vejo isso.",
               "Deixa eu ver isso aqui.",
               "Só um segundo, já vejo.",
               "Peraí, já vou ver isso."],
        "en": ["Just a moment, one second.",
               "One second, checking that now.",
               "Hold on, let me look."],
        "es": ["Un momento, ya lo miro.",
               "Dame un segundo, lo reviso.",
               "Espera un momento, ya veo."],
        "fr": ["Un instant, je regarde ça.",
               "Une seconde, je vérifie ça.",
               "Je regarde ça, un instant."],
        "de": ["Einen Moment, ich schaue nach.",
               "Gleich, ich prüfe das kurz.",
               "Gib mir kurz einen Moment."],
        "it": ["Un momento, lo controllo subito.",
               "Dammi un secondo, controllo subito.",
               "Solo un attimo, guardo subito."],
        "tr": ["Bir saniye bekle, hemen bakıyorum.",
               "Bir dakika, buna hemen bakıyorum.",
               "Bir an bekle, hemen kontrol ediyorum."],
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
# "hold" tops at 2.2 s (T030): the cue ends by the time a typical answer starts, so the
# model's voice does not land on top of the phrase's tail (real test 2026-10-19).
_DURATION_S = {"hold": (0.6, 2.2), "giveup": (0.6, 6.0)}
_SILENCE = 0.02                 # samples under 2% of the absolute peak are silence

_last_variant: dict[str, int] = {}      # kind -> variant played last (pcm_for avoids repeating it)


def _variants(kind: str, lang: str) -> list[str]:
    return TEXTS[kind].get(lang) or TEXTS[kind]["en"]


_SOURCES = ("live", "tts")      # preference order: the conversation's own model first


def phrase_path(kind: str, index: int, voice: str, lang: str, src: str = "tts") -> Path:
    """``src`` = ``live`` (captured from the Live model, the voice of the conversation) or
    ``tts`` (the TTS ladder, the reserve)."""
    from core import voice_preview          # CACHE_DIR is read at call time (tests swap it)
    # "v4": shorter "hold" phrases (T030); the v1-v3 files are never read again and can
    # be deleted by hand.
    return voice_preview.CACHE_DIR / f"wait_v4_{src}_{kind}_{index}_{voice}_{lang}.wav"


def _to_rate(pcm: np.ndarray, rate: int) -> np.ndarray:
    """Linear interpolation to 24 kHz: a spoken phrase does not need better, and
    numpy is already a dependency (no resampler library for two sentences)."""
    if rate == RATE or pcm.size == 0:
        return pcm.astype(np.int16, copy=False)
    n = int(round(pcm.size * RATE / rate))
    x = np.linspace(0.0, pcm.size - 1, num=max(n, 1))
    return np.interp(x, np.arange(pcm.size), pcm.astype(np.float32)).astype(np.int16)


def load_phrase(kind: str, index: int, voice: str, lang: str,
                src: str | None = None) -> np.ndarray | None:
    """Cached phrase as int16 @ 24 kHz, or None (no file, or one that cannot be read).
    ``src=None`` prefers the ``live`` file and falls back to ``tts``."""
    from core import voice_preview
    for one in ((src,) if src else _SOURCES):
        path = phrase_path(kind, index, voice, lang, one)
        try:
            if not path.exists():
                continue
            pcm, rate = voice_preview._read_wav(path)
            if pcm.size:
                return _to_rate(pcm, rate)
        except Exception:
            continue           # a corrupt file is the same as no file: next source, then the tone
    return None


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


def pcm_for(kind: str, voice: str, lang: str, rng: random.Random | None = None,
            with_text: bool = False):
    """What to play for ``kind``: ``(pcm, "voice")`` from the cache, else ``(tone(), "tone")``.
    ``with_text=True`` adds the phrase text as a third value (``""`` for the tone): the
    caller compares it with what the server heard back (``echo_suspect``).

    Draws among the variants that are cached, never the one played last for this
    ``kind`` (with a single cached variant it repeats). Never touches the network and
    never raises. ``rng`` is injectable for tests."""
    try:
        pick = rng or random
        cached = [i for i in range(len(_variants(kind, lang)))
                  if any(phrase_path(kind, i, voice, lang, src).exists() for src in _SOURCES)]
        last = _last_variant.get(kind)
        pool = [i for i in cached if i != last] or cached
        while pool:
            i = pick.choice(pool)
            pcm = load_phrase(kind, i, voice, lang)
            if pcm is not None:
                _last_variant[kind] = i
                return (pcm, "voice", _variants(kind, lang)[i]) if with_text else (pcm, "voice")
            pool.remove(i)         # unreadable file: try another variant
    except Exception:
        pass
    return (tone(), "tone", "") if with_text else (tone(), "tone")


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


# ── the voice of the conversation: capture the phrases from the Live model (R4c) ──
# The TTS models (gemini-*-tts) speak the same voice NAME as the Live model of the
# conversation but with another timbre and prosody (Irlan, real test of 2026-10-19: "it
# sounds like someone else"). So the phrases are captured from the Live model itself, on
# a short extra connection with the same voice and no tools; the TTS stays as the reserve.
_LANG_NAMES = {"pt": "Brazilian Portuguese", "en": "English", "es": "Spanish", "fr": "French",
               "de": "German", "it": "Italian", "tr": "Turkish"}
_MATCH_MIN = 0.85       # a faithful take scores ~1.0; a swapped or mangled phrase ("A momento")
                        # falls well under it. Only a guard against the model improvising.

_live_tried: set[tuple[str, str]] = set()    # (voice, lang) already attempted in this run: the
                                             # quota is not hammered at every reconnection


def _norm(text: str) -> str:
    """Lowercase, no accents, no punctuation, single spaces: how a transcript is compared."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^\w\s]|_", " ", t)
    return " ".join(t.split())


def _matches(expected: str, heard: str) -> bool:
    """The Live transcript says the phrase that was asked for (>= 0.85 similarity)."""
    a, b = _norm(expected), _norm(heard)
    return bool(a and b) and difflib.SequenceMatcher(None, a, b).ratio() >= _MATCH_MIN


# ── the phrase must not come back through the microphone (R3b, T031) ─────────
# A/B test of 2026-10-19: with the phrase on, the model "repeated that it would look" after a
# search instead of giving the result; with it off, it answered normally. The playback batches
# 200 ms and the device adds 213 ms of output latency (both measured), so the tail of the
# phrase is still audible after the queue empties; the 463 ms echo tail was not enough. It
# reached the server as user speech in the middle of the tool call. So the mic is muted hard
# for the phrase's length + the output latency + this margin.
CUE_MUTE_MARGIN_S = 0.6
_LATENCY_FALLBACK_S = 0.25      # a bit above the 213 ms measured, for when there is no measure
_ECHO_RATIO = 0.6               # lower than _MATCH_MIN: the server hears a clipped, reverberated tail


def cue_mute_s(duration_s: float, out_latency_s) -> float:
    """Seconds the microphone stays muted from the moment the phrase is queued."""
    try:
        lat = float(out_latency_s)
        if not lat >= 0.0:          # negative or NaN
            lat = _LATENCY_FALLBACK_S
    except (TypeError, ValueError):
        lat = _LATENCY_FALLBACK_S
    return float(duration_s) + lat + CUE_MUTE_MARGIN_S


def echo_suspect(heard: str, played: str) -> bool:
    """``heard`` (an input transcription) looks like the phrase we played: similar overall,
    or a piece of it with at least two words (the transcript is usually only the tail)."""
    a, b = _norm(heard), _norm(played)
    if not a or not b:
        return False
    if len(a.split()) >= 2 and f" {a} " in f" {b} ":
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= _ECHO_RATIO


def _live_config(voice: str, lang: str) -> dict:
    """Mirror of the session config (main.py ``_build_config``) cut to what a speech engine
    needs: the same voice, audio out, output transcription. No ``language_code`` (the main
    session does not pin one either: the language comes from the line), no tools, none of
    the JARVIS prompt."""
    name = _LANG_NAMES.get(lang, "English")
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": voice}}},
        "output_audio_transcription": {},
        "system_instruction": ("You are a speech engine. When the user sends a line, say exactly "
                               f"that line, word for word, in {name}, at a natural, brisk pace, and nothing else."),
    }


def _real_connect() -> Callable:
    """``connect(model, config_dict)`` over the real SDK; imported here so the module (and
    its tests) never load google.genai."""
    from google import genai
    from google.genai import types
    from core import gemini
    key = gemini.api_key()
    if not key:
        raise RuntimeError("no Gemini API key")
    client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
    return lambda model, config: client.aio.live.connect(
        model=model, config=types.LiveConnectConfig(**config))


def live_capture(texts: list[str], *, model: str, voice: str, lang: str,
                 connect: Callable | None = None,
                 timeout_s: float = 15.0) -> list[tuple[np.ndarray | None, str, str | None]]:
    """Have the Live model say each line, over ONE connection.

    Returns, per text, ``(pcm int16 @ 24 kHz or None, transcription, error or None)``.
    Synchronous from outside (``asyncio.run`` in the calling thread: the prefetch daemon),
    and it never raises. A connection failure marks every line ``"connect: <msg>"``; a line
    that times out or fails ends the session (it is left mid-turn, so the next line would
    get the tail of this one) and the lines after it get ``"skipped: ..."``. One
    ``oneshot`` usage row per line, with the Live model and the reply's ``usage_metadata``.
    ``connect(model, config_dict)`` is injectable for tests (default: the real SDK)."""
    try:
        return asyncio.run(_capture(texts, model, voice, lang, connect, timeout_s))
    except Exception as e:
        return [(None, "", f"connect: {str(e)[:160]}")] * len(texts)


async def _capture(texts, model, voice, lang, connect, timeout_s):
    from core import llm_usage
    results: list[tuple[np.ndarray | None, str, str | None]] = []
    try:
        cm = (connect or _real_connect())(model, _live_config(voice, lang))
        session = await cm.__aenter__()
    except Exception as e:
        return [(None, "", f"connect: {str(e)[:160]}")] * len(texts)

    async def one(text: str):
        chunks, heard, usage = [], [], None
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
        async for r in session.receive():
            if getattr(r, "data", None):
                chunks.append(r.data)
            sc = getattr(r, "server_content", None)
            ot = getattr(sc, "output_transcription", None)
            if getattr(ot, "text", None):
                heard.append(ot.text)
            if getattr(r, "usage_metadata", None):
                usage = r.usage_metadata
            if getattr(sc, "turn_complete", False):
                break
        return b"".join(chunks), "".join(heard).strip(), usage

    broken = None
    try:
        for text in texts:
            if broken:
                results.append((None, "", f"skipped: {broken}"))
                continue
            t0, usage, outcome = time.monotonic(), None, "ok"
            try:
                raw, heard, usage = await asyncio.wait_for(one(text), timeout_s)
                if len(raw) < 2:
                    results.append((None, heard, "no audio"))
                    outcome = "error"
                else:
                    results.append((np.frombuffer(raw[:len(raw) // 2 * 2], dtype=np.int16).copy(),
                                    heard, None))
            except asyncio.TimeoutError:
                broken, outcome = f"timeout after {timeout_s:g}s", "timeout"
                results.append((None, "", broken))
            except Exception as e:
                broken, outcome = f"live error: {str(e)[:120]}", llm_usage.result_of(e)
                results.append((None, "", broken))
            llm_usage.record_oneshot(model, None, outcome,
                                     int((time.monotonic() - t0) * 1000), usage)
    finally:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:
            pass
    return results


def prefetch(voice: str, lang: str, on_error: Callable[[str, str], None] | None = None,
             synth: Callable | None = None, *, live_model: str | None = None,
             connect: Callable | None = None) -> threading.Thread | None:
    """Generate the phrase variants that are not cached yet, off the calling thread.

    Two stages in the daemon thread. (a) With ``live_model``, and only once per run for
    each (voice, lang) (``_live_tried``), the variants without a ``live`` file are
    captured from the Live model, the same one that speaks the conversation; each take
    must pass ``_matches`` (the transcript says the asked phrase) and ``_trim_and_check``
    before it is written. (b) Then the TTS ladder fills what has no file of any source.
    A rejected or failed variant goes to ``on_error(kind, message)`` and leaves no file
    (``_write_wav`` is atomic); a failed Live connection is reported once as
    ``on_error("live", message)`` and stage (b) carries on.

    Returns the thread, or None when nothing is missing. The cache is by voice, not by
    model: changing ``LIVE_MODEL`` (or wanting new takes) means deleting
    ``config/voice_cache/wait_v4_live_*``. ``synth``/``connect`` are injectable for tests;
    the default ``synth`` is the picker's TTS with the bare text and the language code.
    """
    variants = [(kind, i, text) for kind in TEXTS
                for i, text in enumerate(_variants(kind, lang))]
    # An existing but unreadable or empty file counts as missing: otherwise a bad cache
    # would mean the tone forever, with nothing ever regenerating it.
    live_todo = []
    if live_model and (voice, lang) not in _live_tried:
        live_todo = [v for v in variants if load_phrase(v[0], v[1], voice, lang, "live") is None]
        if live_todo:
            _live_tried.add((voice, lang))
    if not live_todo and all(load_phrase(k, i, voice, lang) is not None for k, i, _ in variants):
        return None

    def report(kind: str, msg: str) -> None:
        if on_error:
            try:
                on_error(kind, msg[:160])
            except Exception:
                pass

    def save(kind: str, i: int, src: str, pcm: np.ndarray, rate: int) -> None:
        from core import voice_preview
        pcm = _trim_and_check(kind, pcm, rate)
        voice_preview._write_wav(phrase_path(kind, i, voice, lang, src), _to_rate(pcm, rate), RATE)

    def live_stage() -> None:
        got = live_capture([t for _, _, t in live_todo], model=live_model, voice=voice,
                           lang=lang, connect=connect)
        if got and all((e or "").startswith("connect:") for _, _, e in got):
            return report("live", got[0][2])
        for (kind, i, text), (pcm, heard, err) in zip(live_todo, got):
            try:
                if err:
                    raise RuntimeError(err)
                if not _matches(text, heard):
                    raise RuntimeError(f"live said {heard!r}, not the asked phrase")
                save(kind, i, "live", pcm, RATE)
            except Exception as e:
                report(kind, str(e))

    def work() -> None:
        from core import voice_preview
        if live_todo:
            try:
                live_stage()
            except Exception as e:
                report("live", str(e))
        fn = synth or voice_preview._synthesise
        code = BCP47.get(lang, "en-US")
        for kind, i, text in variants:
            if load_phrase(kind, i, voice, lang) is not None:
                continue            # a live take (or an earlier tts one) already covers it
            try:
                pcm, rate = fn(voice, lang, text=text, style=None, language_code=code)
                if pcm is None or len(pcm) == 0:
                    raise RuntimeError("TTS returned no audio")
                save(kind, i, "tts", pcm, rate)
            except Exception as e:
                report(kind, str(e))

    th = threading.Thread(target=work, daemon=True, name="wait-cue-prefetch")
    th.start()
    return th
