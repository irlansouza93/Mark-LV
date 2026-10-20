"""core/llm_usage.py — the voice_wait record (write side) and how summarize() treats it.

Same errlog setup as tests/test_llm_usage.py: real writer thread, tmp log folder, no network."""
import json
from datetime import timezone

import pytest

from core import errlog, llm_usage

UTC = timezone.utc

REC = {"source": "voice", "outcome": "audio", "cancel_reason": None, "latency_ms": 2310,
       "tool": True, "thinking": True, "phrase": None, "gave_up": False, "enabled": True,
       "limits": {"thinking_ms": 1500, "phrase_ms": 4000, "give_up_ms": 20000}}


@pytest.fixture
def usage_on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({"v": 1, "checked": "2026-10-16", "source": ["test"],
                                  "unit": "usd_per_1m_tokens", "models": {}}), encoding="utf-8")
    monkeypatch.setattr(llm_usage, "PRICES_PATH", prices)
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    errlog.setup(log_dir, tee_console=False)
    yield log_dir
    llm_usage._reset_for_tests()


def usage_lines(log_dir):
    out = []
    for f in sorted(log_dir.glob("usage-2*.jsonl")):
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


# ── write side ───────────────────────────────────────────────────────────────
def test_voice_wait_is_queued_with_kind_provider_and_the_episode_fields(usage_on):
    llm_usage.record_voice_wait(dict(REC))
    errlog.flush()
    (r,) = usage_lines(usage_on)
    assert (r["kind"], r["provider"]) == ("voice_wait", "gemini")
    for key, value in REC.items():
        assert r[key] == value, key
    assert r["v"] == 1 and "ts" in r


def test_model_comes_from_the_open_live_session(usage_on):
    llm_usage.live_session_start("models/gemini-live-x", "Puck", False)
    llm_usage.record_voice_wait(dict(REC))
    errlog.flush()
    wait = [r for r in usage_lines(usage_on) if r["kind"] == "voice_wait"]
    assert wait[0]["model"] == "gemini-live-x"


def test_without_a_session_the_model_is_gemini_live(usage_on):
    llm_usage.record_voice_wait(dict(REC))
    errlog.flush()
    (r,) = usage_lines(usage_on)
    assert r["model"] == "gemini-live"


def test_record_voice_wait_never_raises_nor_mutates_the_callers_dict(usage_on):
    rec = dict(REC)
    llm_usage.record_voice_wait(rec)
    assert rec == REC
    llm_usage.record_voice_wait(None)                        # garbage in: still no exception


# ── read side ────────────────────────────────────────────────────────────────
def _row(kind, **kw):
    base = {"v": 1, "ts": "2026-10-19T10:00:00.000+00:00", "provider": "gemini", "model": "m", "kind": kind}
    base.update(kw)
    return base


def test_summarize_ignores_voice_wait_without_counting_it_as_skipped_or_as_a_call():
    base = [_row("oneshot", result="ok", latency_ms=10, billing="free", tokens=None,
                 cost_usd_if_paid=None, cost_usd_actual=0.0, price_missing=False)]
    plain = llm_usage.summarize(base, UTC)
    mixed = llm_usage.summarize(base + [_row("voice_wait", **REC), _row("voice_wait", **REC)], UTC)
    assert mixed == plain
    assert mixed["skipped"] == 0


def test_summarize_with_only_voice_wait_has_no_rows_and_no_skips():
    got = llm_usage.summarize([_row("voice_wait", **REC)], UTC)
    assert got["skipped"] == 0 and got["rows"] == []


def test_summarize_still_skips_unknown_kinds():
    got = llm_usage.summarize([_row("voice_wait", **REC), _row("mystery")], UTC)
    assert got["skipped"] == 1
