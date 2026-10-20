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


# ── wait_summary(): the latency summary (spec 002, US3) ──────────────────────────────────
def _wait(latency=1000, tool=False, outcome="audio", **kw):
    r = dict(REC, latency_ms=latency, tool=tool, outcome=outcome, thinking=False, phrase=None, gave_up=False)
    r.update(kw)
    return _row("voice_wait", **r)


def test_percentiles_use_the_nearest_rank_on_the_sorted_list():
    # 10 values, deliberately unsorted: nearest rank = ceil(p/100 * n)-th smallest
    values = [700, 100, 1000, 300, 500, 900, 200, 800, 400, 600]
    got = llm_usage.wait_summary([_wait(v) for v in values])
    assert got["no_tool"] == {"n": 10, "p50": 500, "p90": 900, "p95": 1000}


def test_a_single_episode_is_every_percentile():
    got = llm_usage.wait_summary([_wait(2310)])
    assert got["no_tool"] == {"n": 1, "p50": 2310, "p90": 2310, "p95": 2310}


def test_tool_and_no_tool_are_kept_apart():
    got = llm_usage.wait_summary([_wait(1000), _wait(2000), _wait(9000, tool=True)])
    assert got["no_tool"]["n"] == 2 and got["tool"] == {"n": 1, "p50": 9000, "p90": 9000, "p95": 9000}


def test_a_group_with_no_episodes_is_none():
    got = llm_usage.wait_summary([_wait(1000)])
    assert got["tool"] is None
    got = llm_usage.wait_summary([_wait(1000, tool=True)])
    assert got["no_tool"] is None


def test_outcomes_and_fired_counters():
    recs = [
        _wait(1200, thinking=True),
        _wait(3000, thinking=True, phrase="voice", tool=True),
        _wait(4500, thinking=True, phrase="tone"),
        _wait(None, outcome="no_reply", thinking=True, phrase="voice"),
        _wait(None, outcome="gave_up", thinking=True, phrase="voice", gave_up=True),
        _wait(None, outcome="cancelled", cancel_reason="interrupt"),
    ]
    got = llm_usage.wait_summary(recs)
    assert got["episodes"] == 6 and got["with_latency"] == 3
    assert got["outcomes"] == {"audio": 3, "no_reply": 1, "gave_up": 1, "cancelled": 1}
    assert got["fired"] == {"thinking": 5, "phrase": 4, "phrase_tone": 1, "gave_up": 1}


def test_records_that_are_not_voice_wait_or_have_no_usable_latency_stay_out_of_the_percentiles():
    recs = [
        _row("live_turn", latency_ms=99999),                  # old live_turn with a latency: not an episode
        _row("oneshot", latency_ms=99999),
        "not a dict", None,
        _wait(None, outcome="cancelled"),                      # no latency
        _wait(True),                                           # a bool is not a latency
        _wait("2000"),                                         # nor a string
        _wait(1500),
    ]
    got = llm_usage.wait_summary(recs)
    assert got["episodes"] == 4 and got["with_latency"] == 1
    assert got["no_tool"] == {"n": 1, "p50": 1500, "p90": 1500, "p95": 1500}


def test_empty_input():
    got = llm_usage.wait_summary([])
    assert got["episodes"] == 0 and got["with_latency"] == 0
    assert got["no_tool"] is None and got["tool"] is None and got["suggested"] is None
    assert got["outcomes"] == {"audio": 0, "no_reply": 0, "gave_up": 0, "cancelled": 0}
    assert got["fired"] == {"thinking": 0, "phrase": 0, "phrase_tone": 0, "gave_up": 0}
    assert got["suggest_note"] == "dados insuficientes (0 de 30)"


def test_no_suggestion_below_thirty_episodes_with_latency():
    got = llm_usage.wait_summary([_wait(1000) for _ in range(29)] + [_wait(None, outcome="no_reply")])
    assert got["with_latency"] == 29 and got["suggested"] is None
    assert got["suggest_note"] == "dados insuficientes (29 de 30)"


def test_suggestion_appears_at_thirty():
    got = llm_usage.wait_summary([_wait(1000) for _ in range(30)])
    assert got["suggested"] is not None
    assert "insuficientes" not in got["suggest_note"] and got["suggest_note"]


def _suggest(*groups):
    """groups: (latency, n, tool) triples -> the suggested limits."""
    recs = [_wait(lat, tool=tool) for lat, n, tool in groups for _ in range(n)]
    got = llm_usage.wait_summary(recs)
    assert got["with_latency"] >= 30
    return got["suggested"]


def test_hud_limit_is_the_no_tool_p90_rounded_to_100_ms():
    s = _suggest((1234, 30, False))
    assert s["thinking_ms"] == 1200
    assert s["phrase_ms"] == 2700                      # max(1200 + 1500, p90 of all = 1234)
    assert s["give_up_ms"] == 20000                    # max(20000, 1234 + 5000)


def test_hud_limit_is_clamped_to_800_3000():
    assert _suggest((300, 30, False))["thinking_ms"] == 800
    assert _suggest((5000, 30, False))["thinking_ms"] == 3000


def test_phrase_limit_is_max_of_hud_plus_1500_and_p90_of_all_within_2500_8000():
    low = _suggest((300, 30, False))
    assert low["phrase_ms"] == 2500                    # max(800 + 1500, 300) = 2300 -> floor 2500
    mid = _suggest((5000, 30, False))
    assert mid["phrase_ms"] == 5000                    # max(3000 + 1500, 5000)
    # tool turns push the p90 of ALL (but not the no-tool HUD limit): 15 x 1000 + 15 x 10000
    high = _suggest((1000, 15, False), (10000, 15, True))
    assert high["thinking_ms"] == 1000
    assert high["phrase_ms"] == 8000                   # p90 of all = 10000 -> cap 8000


def test_give_up_limit_is_max_of_20000_and_p99_plus_5000_capped_at_60000():
    assert _suggest((50000, 30, False))["give_up_ms"] == 55000
    assert _suggest((70000, 30, False))["give_up_ms"] == 60000
    assert _suggest((10000, 30, False))["give_up_ms"] == 20000


def test_suggestion_without_any_no_tool_episode_falls_back_to_p90_of_all_for_the_hud():
    s = _suggest((2000, 30, True))
    assert s["thinking_ms"] == 2000
