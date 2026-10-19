"""core/voice_preview.py records TTS usage (T030), and memory.config_manager.get_llm_billing (T026).

google.genai is replaced by a stub: no network, no key."""
import json
import sys
import types

import pytest

from core import errlog, gemini, llm_usage, voice_preview
from memory import config_manager


@pytest.fixture
def usage(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"models": {"gemini/gemini-3.8-flash-tts": {"in": 0.5, "out": 9.0}}}), encoding="utf-8")
    monkeypatch.setattr(llm_usage, "PRICES_PATH", prices)
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    monkeypatch.setattr(gemini, "api_key", lambda refresh=False: "AIza" + "x" * 35)
    errlog.setup(log_dir, tee_console=False)
    yield log_dir
    llm_usage._reset_for_tests()


def stub_genai(monkeypatch, behaviour):
    """behaviour: model name -> exception to raise, or a response object."""
    calls = []

    class Models:
        @staticmethod
        def generate_content(model, contents, config):
            calls.append(model)
            out = behaviour[model]
            if isinstance(out, Exception):
                raise out
            return out

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
    return calls


def audio_reply(usage_metadata):
    part = types.SimpleNamespace(data=b"\x01\x00" * 480, mime_type="audio/L16;rate=24000")
    cand = types.SimpleNamespace(content=types.SimpleNamespace(parts=[types.SimpleNamespace(inline_data=part)]))
    return types.SimpleNamespace(candidates=[cand], usage_metadata=usage_metadata)


def records(log_dir):
    out = []
    for f in sorted(log_dir.glob("usage-2*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def test_tts_success_and_failed_rung_are_both_recorded(usage, monkeypatch):
    um = types.SimpleNamespace(prompt_token_count=20, candidates_token_count=300,
                               candidates_tokens_details=[{"modality": "AUDIO", "token_count": 300}])
    calls = stub_genai(monkeypatch, {
        "gemini-3.8-flash-lite-tts": Exception("429 RESOURCE_EXHAUSTED"),
        "gemini-3.8-flash-tts": audio_reply(um),
    })
    pcm, rate = voice_preview._synthesise("Puck", "en")
    assert rate == 24000 and len(pcm) == 480                               # the sample itself is unchanged
    errlog.flush()
    quota, ok = records(usage)
    assert calls == ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts"]
    assert (quota["kind"], quota["model"], quota["result"], quota["tokens"]) == \
        ("tts", "gemini-3.8-flash-lite-tts", "quota", None)
    assert (ok["kind"], ok["model"], ok["result"]) == ("tts", "gemini-3.8-flash-tts", "ok")
    assert ok["tokens"]["out_audio"] == 300 and ok["tokens"]["in_text"] == 20
    assert ok["cost_usd_if_paid"] == pytest.approx((20 * 0.5 + 300 * 9.0) / 1_000_000)
    assert isinstance(ok["latency_ms"], int)


def test_every_tts_model_failing_records_each_one(usage, monkeypatch):
    stub_genai(monkeypatch, {m: Exception("503 UNAVAILABLE") for m in voice_preview.TTS_MODELS})
    with pytest.raises(Exception):
        voice_preview._synthesise("Puck", "en")
    errlog.flush()
    assert [r["model"] for r in records(usage)] == list(voice_preview.TTS_MODELS)
    assert {r["result"] for r in records(usage)} == {"unavailable"}


# ── get_llm_billing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("stored, expected", [
    ({"llm_billing": "paid"}, "paid"),
    ({"llm_billing": "free"}, "free"),
    ({}, "free"),
    ({"llm_billing": "gold"}, "free"),
    ({"llm_billing": 7}, "free"),
    ({"llm_billing": None}, "free"),
])
def test_get_llm_billing(tmp_path, monkeypatch, stored, expected):
    path = tmp_path / "api_keys.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", path)
    assert config_manager.get_llm_billing() == expected


def test_get_llm_billing_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "CONFIG_FILE", tmp_path / "missing.json")
    assert config_manager.get_llm_billing() == "free"
