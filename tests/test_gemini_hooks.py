"""Failure hooks in core/gemini.py's ladder (spec FR-004, FR-004a). No network, no SDK:
`client` and `_live_call` are replaced, so every rung fails the way the test says."""
import asyncio
import json
import re

import pytest

from core import errlog, gemini

KEY = "AIza" + "x" * 35


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def gem(log_dir, tmp_path, monkeypatch):
    """errlog on in tmp_path + a clean cooldown table before AND after (module-level state
    that would otherwise leak a resting rung into the next test)."""
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": KEY}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    monkeypatch.setattr(gemini, "api_key", lambda refresh=False: KEY)
    gemini._cooldown.clear()
    yield gemini
    gemini._cooldown.clear()


def fail_every_rung(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(gemini, "client", boom)
    monkeypatch.setattr(gemini, "_live_call", boom)


def failures(log_dir):
    return read_lines(log_dir / "failures.jsonl")


def test_every_rung_and_the_exhausted_ladder_are_recorded(gem, log_dir, monkeypatch, capsys):
    fail_every_rung(monkeypatch, Exception(f"429 RESOURCE_EXHAUSTED quota for key={KEY}"))
    assert gem.call("oi", tier=gem.FAST) is None
    errlog.flush()
    recs = failures(log_dir)
    ladder = gem._LADDERS[gem.FAST]
    rungs = [r for r in recs if r["name"] != "ladder"]
    assert [r["name"] for r in rungs] == list(ladder)
    for r in rungs:
        assert r["kind"] == "gemini" and r["component"] == "gemini" and r["error_type"] == "quota"
        assert r["context"]["tier"] == "fast" and r["context"]["timeout_ms"] == gem.DEFAULT_TIMEOUT_MS
        assert isinstance(r["context"]["latency_ms"], int)
        assert r["origin"] == "core/gemini.py:call"
    (end,) = [r for r in recs if r["name"] == "ladder"]
    assert end["error_type"] == "ladder_exhausted"
    assert end["context"]["tried"] == list(ladder)
    assert end["context"]["reasons"] == {m: "quota" for m in ladder}
    assert "out of quota" in capsys.readouterr().out                 # the old prints are untouched
    assert KEY not in (log_dir / "failures.jsonl").read_text(encoding="utf-8")


def test_screen_line_appears_once_even_if_call_runs_twice(gem, log_dir, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)
    fail_every_rung(monkeypatch, Exception("429 RESOURCE_EXHAUSTED"))
    assert gem.call("a", tier=gem.FAST) is None
    assert gem.call("b", tier=gem.FAST) is None
    errlog.flush()
    assert len(seen) == 1
    assert seen[0].startswith("SYS: Gemini free quota used up")
    assert re.search(r"back at \d\d:\d\d\.$", seen[0])


def test_unavailable_models_get_the_other_message(gem, log_dir, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)
    fail_every_rung(monkeypatch, Exception("503 UNAVAILABLE. The model is overloaded."))
    assert gem.call("x", tier=gem.SMART) is None
    errlog.flush()
    assert seen == [f"SYS: Gemini models not answering ({len(gem._LADDERS[gem.SMART])} tried) — try again shortly."]
    assert {r["error_type"] for r in failures(log_dir) if r["name"] != "ladder"} == {"unavailable"}


@pytest.mark.parametrize("exc, expected", [
    (asyncio.TimeoutError(), "timeout"),
    (TimeoutError("the read operation timed out"), "timeout"),
    (RuntimeError("404 NOT_FOUND model"), "gone"),
    (ValueError("something else broke"), "error"),
])
def test_other_reasons_are_classified(gem, log_dir, monkeypatch, exc, expected):
    fail_every_rung(monkeypatch, exc)
    assert gem.call("x", tier=gem.FAST) is None
    errlog.flush()
    assert {r["error_type"] for r in failures(log_dir) if r["name"] != "ladder"} == {expected}


def test_a_working_rung_after_failures_records_only_the_failures(gem, log_dir, monkeypatch):
    class Reply:
        text = "fine"

    class FakeClient:
        class models:
            @staticmethod
            def generate_content(**kw):
                return Reply()

    monkeypatch.setattr(gem, "_live_call", lambda *a, **k: (_ for _ in ()).throw(Exception("503 UNAVAILABLE")))
    monkeypatch.setattr(gem, "client", lambda **k: FakeClient)
    assert gem.call("x", tier=gem.FAST).text == "fine"
    errlog.flush()
    recs = failures(log_dir)
    assert [r["name"] for r in recs] == [gem.LIVE]                   # no "ladder_exhausted" on success
    assert recs[0]["error_type"] == "unavailable"


def test_missing_key_is_recorded_and_announced(gem, log_dir, monkeypatch, capsys):
    seen = []
    errlog.bind(notify=seen.append)
    monkeypatch.setattr(gem, "api_key", lambda refresh=False: "")
    assert gem.call("x") is None
    errlog.flush()
    (rec,) = failures(log_dir)
    assert rec["component"] == "gemini" and rec["error_type"] == "no_key"
    assert seen and seen[0].startswith("SYS:") and "API key" in seen[0]
    assert "no Gemini API key is configured" in capsys.readouterr().out


def test_cooldown_is_clean_between_tests(gem):
    assert gem._cooldown == {}


# ── usage records (US2, T023/T029) ───────────────────────────────────────────
import sys
import types

from core import llm_usage

M = 1_000_000
PRICE_TABLE = {"v": 1, "checked": "2026-10-16", "source": ["t"], "models": {
    "gemini/gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40},
    "gemini/gemini-3.1-flash-live-preview": {"in": {"text": 0.75, "audio": 3.0},
                                             "out": {"text": 4.5, "audio": 12.0}},
}}


@pytest.fixture
def usage(gem, log_dir, tmp_path, monkeypatch):
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICE_TABLE), encoding="utf-8")
    monkeypatch.setattr(llm_usage, "PRICES_PATH", prices)
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    yield log_dir
    llm_usage._reset_for_tests()


def usage_records(log_dir):
    out = []
    for f in sorted(log_dir.glob("usage-2*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


class RestReply:
    text = "hello"

    def __init__(self, usage_metadata):
        self.usage_metadata = usage_metadata


def fake_client(reply=None, fail=None):
    calls = []

    class Models:
        @staticmethod
        def generate_content(**kw):
            calls.append(kw["model"])
            if fail and fail.get(kw["model"]):
                raise fail[kw["model"]]
            return reply

    return (lambda **k: types.SimpleNamespace(models=Models)), calls


def test_rest_success_writes_one_oneshot_usage_record(usage, monkeypatch):
    um = types.SimpleNamespace(prompt_token_count=M, candidates_token_count=M // 2)
    client, _ = fake_client(RestReply(um))
    monkeypatch.setattr(gemini, "client", client)
    monkeypatch.setattr(gemini, "_live_call", lambda *a, **k: (_ for _ in ()).throw(Exception("503 UNAVAILABLE")))
    reply = gemini.call("hi", tier=gemini.FAST)
    assert reply.text == "hello" and reply.usage_metadata is um               # what callers get is unchanged
    errlog.flush()
    live_fail, ok = usage_records(usage)
    assert live_fail["kind"] == "oneshot" and live_fail["result"] == "unavailable"
    assert live_fail["model"] == "gemini-3.1-flash-live-preview" and live_fail["tokens"] is None
    assert (ok["kind"], ok["provider"], ok["model"], ok["tier_requested"]) == \
        ("oneshot", "gemini", "gemini-2.5-flash-lite", "fast")
    assert (ok["billing"], ok["result"]) == ("free", "ok")
    assert isinstance(ok["latency_ms"], int)
    assert ok["tokens"]["in_text"] == M and ok["tokens"]["out_text"] == M // 2
    assert ok["cost_usd_if_paid"] == pytest.approx(0.10 + 0.20) and ok["cost_usd_actual"] == 0.0
    assert ok["price_missing"] is False


def test_quota_before_the_answer_is_recorded_without_tokens(usage, monkeypatch):
    um = types.SimpleNamespace(prompt_token_count=10, candidates_token_count=5)
    client, calls = fake_client(RestReply(um), fail={"gemini-2.5-flash-lite": Exception("429 RESOURCE_EXHAUSTED")})
    monkeypatch.setattr(gemini, "client", client)
    monkeypatch.setattr(gemini, "_live_call", lambda *a, **k: None)           # empty Live turn -> falls through
    assert gemini.call("hi", tier=gemini.FAST).text == "hello"
    errlog.flush()
    recs = usage_records(usage)
    quota = [r for r in recs if r["result"] == "quota"]
    assert len(quota) == 1 and quota[0]["model"] == "gemini-2.5-flash-lite" and quota[0]["tokens"] is None
    assert quota[0]["cost_usd_if_paid"] is None and quota[0]["price_missing"] is False
    assert [r["model"] for r in recs if r["result"] == "ok"] == ["gemini-3.5-flash-lite"]
    assert [r["result"] for r in recs][:2] == ["error", "quota"]               # empty Live turn, then the quota


def test_live_rung_success_records_the_live_model(usage, monkeypatch):
    um = types.SimpleNamespace(prompt_token_count=1000, response_token_count=500,
                               response_tokens_details=[{"modality": "AUDIO", "token_count": 500}])
    monkeypatch.setattr(gemini, "_live_call", lambda *a, **k: gemini._Reply("spoken text", um))
    reply = gemini.call("hi", tier=gemini.SMART)
    assert reply.text == "spoken text"
    errlog.flush()
    (rec,) = usage_records(usage)
    assert rec["model"] == "gemini-3.1-flash-live-preview" and rec["result"] == "ok"
    assert rec["tier_requested"] == "smart"
    assert rec["tokens"]["in_text"] == 1000 and rec["tokens"]["out_audio"] == 500
    assert rec["cost_usd_if_paid"] == pytest.approx((1000 * 0.75 + 500 * 12.0) / M)


def test_reply_keeps_its_text_contract():
    r = gemini._Reply("abc")
    assert r.text == "abc" and r.usage_metadata is None
    assert gemini._Reply("abc", 5).usage_metadata == 5


def test_text_helper_is_unchanged_for_callers(usage, monkeypatch):
    monkeypatch.setattr(gemini, "_live_call", lambda *a, **k: gemini._Reply("  padded  ", None))
    assert gemini.text("x") == "padded"
    assert gemini.as_json("x", default="d") == "d"


def test_live_turn_hands_back_the_last_usage_metadata(monkeypatch):
    """_live_turn is the only place that sees the Live messages of a one-shot session; it must
    return their usage_metadata next to the text. google.genai is replaced by a stub."""
    usage_a = types.SimpleNamespace(prompt_token_count=1)
    usage_b = types.SimpleNamespace(prompt_token_count=9, response_token_count=3)

    def message(text=None, usage_metadata=None):
        sc = types.SimpleNamespace(output_transcription=types.SimpleNamespace(text=text) if text else None)
        return types.SimpleNamespace(server_content=sc, usage_metadata=usage_metadata)

    class Session:
        used = False

        async def send_client_content(self, **kw):
            pass

        async def receive(self):
            if self.used:                                   # the second, short drain finds nothing
                return
            self.used = True
            yield message("Hel", usage_a)
            yield message("lo", None)                       # a message without usage must not erase it
            yield message(None, usage_b)

    class Ctx:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *a):
            return False

    class Client:
        def __init__(self, **kw):
            self.aio = types.SimpleNamespace(live=types.SimpleNamespace(connect=lambda **k: Ctx()))

    fake_genai = types.ModuleType("google.genai")
    fake_genai.Client = Client
    fake_types = types.ModuleType("google.genai.types")
    fake_types.LiveConnectConfig = lambda **kw: kw
    fake_genai.types = fake_types
    fake_google = types.ModuleType("google")
    fake_google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)
    text, usage_meta = asyncio.run(gemini._live_turn([{"text": "hi"}], "", "key", 10.0))
    assert text == "Hello" and usage_meta is usage_b
