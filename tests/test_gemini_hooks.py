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
