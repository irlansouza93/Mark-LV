"""voice_preview._synthesise with an explicit text / language_code (the wait phrases, spec 002 R4b).

google.genai is replaced by a stub that keeps what was sent: no network, no key."""
import sys
import types

import pytest

from core import gemini, llm_usage, voice_preview


@pytest.fixture
def sent(monkeypatch):
    log = {"contents": [], "config": []}

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            log["contents"].append(contents)
            log["config"].append(config)
            part = types.SimpleNamespace(data=b"\x01\x00" * 480, mime_type="audio/L16;rate=24000")
            cand = types.SimpleNamespace(content=types.SimpleNamespace(
                parts=[types.SimpleNamespace(inline_data=part)]))
            return types.SimpleNamespace(candidates=[cand], usage_metadata=None)

    class Client:
        def __init__(self, **kw):
            self.models = Models

    genai = types.ModuleType("google.genai")
    genai.Client = Client
    gtypes = types.ModuleType("google.genai.types")
    for name in ("GenerateContentConfig", "SpeechConfig", "VoiceConfig", "PrebuiltVoiceConfig"):
        setattr(gtypes, name, lambda **kw: kw)
    genai.types = gtypes
    google = types.ModuleType("google")
    google.genai = genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", gtypes)
    monkeypatch.setattr(gemini, "api_key", lambda refresh=False: "AIza" + "x" * 35)
    monkeypatch.setattr(llm_usage, "record_tts", lambda *a, **k: None)
    return log


def test_text_without_style_is_sent_bare(sent):
    voice_preview._synthesise("Puck", "pt", text="Só um momento, já vejo isso.")
    assert sent["contents"] == ["Só um momento, já vejo isso."]       # no "Say this ..." prefix


def test_language_code_goes_in_the_speech_config(sent):
    voice_preview._synthesise("Puck", "pt", text="Só um momento, já vejo isso.", language_code="pt-BR")
    assert sent["config"][0]["speech_config"]["language_code"] == "pt-BR"


def test_no_language_code_leaves_the_speech_config_as_before(sent):
    voice_preview._synthesise("Puck", "en")
    assert "language_code" not in sent["config"][0]["speech_config"]


def test_picker_behaviour_is_unchanged_without_the_new_arguments(sent):
    voice_preview._synthesise("Puck", "en")
    assert sent["contents"] == ["Say this warmly and naturally, like a friend: " + voice_preview._PHRASES["en"]]


def test_explicit_style_is_still_a_prefix(sent):
    voice_preview._synthesise("Puck", "en", text="Hello there my friend", style="Whisper:")
    assert sent["contents"] == ["Whisper: Hello there my friend"]
