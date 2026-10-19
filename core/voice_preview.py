"""
Voice samples for the picker — hear a voice before you commit to it.

The Live session's voice is fixed at connect time, so trying one used to mean
applying it, waiting for the session to rebuild, and starting a conversation.
This speaks one short sentence in the chosen voice instead, through Gemini's
TTS models — the same prebuilt voices the Live models use, so the sample is the
voice you will actually get, not an approximation of it.

* **Cached on disk.** A sample costs ~4 s and one TTS request the first time and
  nothing afterwards: `config/voice_cache/<voice>_<lang>.wav`. Thirty voices are
  roughly 8 MB at 24 kHz mono.
* **In the user's language.** The sentence follows the language stored in
  memory, so a Portuguese speaker judges the voice speaking Portuguese — accent
  is most of what makes a voice sound human or not.
* **Its own ladder.** The TTS models are tried newest first and a failure moves
  to the next one; a quota on one model does not cost the preview.
* **Never on the Qt thread.** `preview()` returns at once; `on_state` is called
  from a worker thread with "loading" / "playing" / "done" / "error:<why>", and
  the caller marshals it onto the UI.
"""

from __future__ import annotations

import io
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
from core import llm_usage

_BASE = Path(__file__).resolve().parent.parent
CACHE_DIR = _BASE / "config" / "voice_cache"

# Newest first. Measured on this project's key: all four answer in ~4 s; the
# 3.8 pair return WAV, the older two return raw 24 kHz PCM — both handled.
TTS_MODELS = (
    "gemini-3.8-flash-lite-tts",
    "gemini-3.8-flash-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
)
_TIMEOUT_MS = 20_000

# One natural sentence per language. Short on purpose: long enough to hear the
# timbre and the rhythm, short enough to compare five voices in a minute.
_PHRASES = {
    "pt": "Olá! Eu sou o seu assistente. Pode contar comigo para o que precisar hoje.",
    "en": "Hello! I'm your assistant. You can count on me for whatever you need today.",
    "es": "¡Hola! Soy tu asistente. Puedes contar conmigo para lo que necesites hoy.",
    "fr": "Bonjour ! Je suis votre assistant. Vous pouvez compter sur moi aujourd'hui.",
    "de": "Hallo! Ich bin dein Assistent. Du kannst heute auf mich zählen.",
    "it": "Ciao! Sono il tuo assistente. Puoi contare su di me per qualsiasi cosa oggi.",
    "tr": "Merhaba! Ben asistanınım. Bugün neye ihtiyacın olursa bana güvenebilirsin.",
}
# Matched against whatever the memory holds — "Portuguese", "português",
# "pt-BR" and "Brazilian Portuguese" all land on the same key.
_LANG_HINTS = (
    ("pt", ("portug", "pt", "brazil", "brasil")),
    ("es", ("spanish", "españ", "espan", "es")),
    ("fr", ("french", "fran", "fr")),
    ("de", ("german", "deutsch", "de")),
    ("it", ("italian", "italiano", "it")),
    ("tr", ("turk", "türk", "tr")),
    ("en", ("english", "inglês", "en")),
)

_lock = threading.Lock()
_gen = 0          # bumped by every preview()/stop(); stale workers stay quiet


def language_code() -> str:
    """Two-letter code for the user's language, from memory; English otherwise."""
    try:
        from memory.memory_manager import load_memory
        e = load_memory().get("identity", {}).get("language", {})
        raw = (e.get("value", "") if isinstance(e, dict) else str(e)).strip().lower()
    except Exception:
        raw = ""
    if raw:
        words = re.split(r"[\s,;/_\-()]+", raw)
        for code, hints in _LANG_HINTS:
            for h in hints:
                # Two-letter hints must match a whole token ("pt", "pt-br");
                # longer ones may be a prefix ("portug" → "portuguese").
                if (len(h) <= 2 and h in words) or (len(h) > 2 and h in raw):
                    return code
    return "en"


def _decode(data: bytes, mime: str) -> tuple[np.ndarray, int]:
    """TTS output → (int16 mono samples, sample rate)."""
    if data[:4] == b"RIFF" or "wav" in (mime or "").lower():
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            ch = w.getnchannels()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if ch > 1:
            pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
        return pcm, rate
    m = re.search(r"rate=(\d+)", mime or "")
    return np.frombuffer(data, dtype=np.int16), int(m.group(1)) if m else 24000


def _write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.astype(np.int16).tobytes())
    tmp.replace(path)          # never leave a half-written sample in the cache


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        return (np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16),
                w.getframerate())


def _synthesise(voice: str, lang: str) -> tuple[np.ndarray, int]:
    from google import genai
    from google.genai import types
    from core import gemini

    key = gemini.api_key()
    if not key:
        raise RuntimeError("no Gemini API key")
    cl = genai.Client(api_key=key, http_options={"timeout": _TIMEOUT_MS})
    text = _PHRASES.get(lang, _PHRASES["en"])
    cfg = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
    )
    last = "no TTS model answered"
    for model in TTS_MODELS:
        t0, r = time.monotonic(), None
        try:
            r = cl.models.generate_content(
                model=model,
                contents=f"Say this warmly and naturally, like a friend: {text}",
                config=cfg)
            llm_usage.record_tts(model, "ok", int((time.monotonic() - t0) * 1000), getattr(r, "usage_metadata", None))
            part = r.candidates[0].content.parts[0].inline_data
            if part and part.data:
                return _decode(part.data, part.mime_type or "")
            last = f"{model}: empty audio"
        except Exception as e:           # quota / 404 / timeout → next rung
            if r is None:                # the call failed (a bad reply after a billed one is not a second use)
                llm_usage.record_tts(model, llm_usage.result_of(e), int((time.monotonic() - t0) * 1000))
            last = f"{model}: {str(e)[:120]}"
    raise RuntimeError(last)


def sample_path(voice: str, lang: str | None = None) -> Path:
    return CACHE_DIR / f"{voice}_{lang or language_code()}.wav"


def preview(voice: str, on_state=None) -> None:
    """Speak a short sample in `voice`. Returns immediately.

    A newer call (or stop()) supersedes an older one: only the latest preview
    plays, so clicking quickly through voices never stacks samples on top of
    each other.
    """
    global _gen
    with _lock:
        _gen += 1
        my = _gen

    def emit(s: str) -> None:
        if on_state and my == _gen:
            try:
                on_state(s)
            except Exception:
                pass

    def work() -> None:
        try:
            import sounddevice as sd
            lang = language_code()
            path = sample_path(voice, lang)
            if path.exists():
                pcm, rate = _read_wav(path)
            else:
                emit("loading")
                pcm, rate = _synthesise(voice, lang)
                _write_wav(path, pcm, rate)
            if my != _gen:
                return
            dev = None
            try:
                from core import audio_devices
                from memory.config_manager import get_output_device
                dev = audio_devices.resolve(get_output_device(), "output")
            except Exception:
                dev = None
            emit("playing")
            try:
                sd.play(pcm, rate, device=dev)
            except Exception:
                sd.play(pcm, rate)            # chosen speaker refused — default
            sd.wait()
            emit("done")
        except Exception as e:
            emit(f"error:{e}")

    threading.Thread(target=work, daemon=True, name="voice-preview").start()


def stop() -> None:
    """Silence any sample that is playing and drop any that is still loading."""
    global _gen
    with _lock:
        _gen += 1
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:
        pass
