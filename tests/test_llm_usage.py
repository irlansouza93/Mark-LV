"""core/llm_usage.py — token extraction, cost, the Live turn meter, the summary (US2).

SDK objects are imitated with SimpleNamespace / dicts: no google.genai, no network."""
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from core import errlog, llm_usage

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))

PRICES = {
    "v": 1, "checked": "2026-10-16", "source": ["test"], "unit": "usd_per_1m_tokens",
    "models": {
        "gemini/m-simple": {"in": 1.0, "out": 4.0},
        "gemini/m-live": {"in": {"text": 0.75, "audio": 3.0, "image": 1.0},
                          "out": {"text": 4.5, "audio": 12.0}},
        "gemini/m-cache": {"in": 0.30, "in_cached": 0.03, "out": 2.5},
        "deepseek/m-peak": {"in": 0.15, "out": 0.60, "peak_multiplier": 2.0},
        "gemini/m-sched": {"in": 0.75, "out": 3.75,
                           "schedule": [{"from": "2027-01-01", "in": 1.5, "out": 7.5}]},
    },
}
M = 1_000_000


def toks(**kw):
    base = dict(in_text=None, in_audio=None, in_image=None, in_cached=None,
                out_text=None, out_audio=None, thoughts=None, total=None)
    base.update(kw)
    return base


# ── tokens_from_usage ────────────────────────────────────────────────────────
def test_tokens_from_rest_usage_with_modalities():
    um = NS(prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=50,
            cached_content_token_count=100, total_token_count=1250,
            prompt_tokens_details=[NS(modality="TEXT", token_count=700),
                                   {"modality": "AUDIO", "token_count": 200},
                                   NS(modality=NS(name="IMAGE"), token_count=100)],
            candidates_tokens_details=[{"modality": "TEXT", "token_count": 200}])
    assert llm_usage.tokens_from_usage(um) == toks(
        in_text=700, in_audio=200, in_image=100, in_cached=100, out_text=200,
        thoughts=50, total=1250)


def test_tokens_from_live_usage():
    um = NS(prompt_token_count=500, response_token_count=300, total_token_count=800,
            response_tokens_details=[{"modality": "AUDIO", "token_count": 250},
                                     {"modality": "TEXT", "token_count": 50}])
    assert llm_usage.tokens_from_usage(um) == toks(in_text=500, out_text=50, out_audio=250, total=800)


def test_without_details_everything_counts_as_text_and_missing_is_none_not_zero():
    got = llm_usage.tokens_from_usage(NS(prompt_token_count=100, candidates_token_count=40))
    assert got["in_text"] == 100 and got["out_text"] == 40
    assert got["in_audio"] is None and got["thoughts"] is None and got["in_cached"] is None
    assert got["total"] == 140                       # derived from what is known


def test_video_and_image_modalities_share_the_image_field():
    um = NS(prompt_token_count=30, prompt_tokens_details=[{"modality": "VIDEO", "token_count": 10},
                                                           {"modality": "IMAGE", "token_count": 20}])
    assert llm_usage.tokens_from_usage(um)["in_image"] == 30


@pytest.mark.parametrize("um", [None, NS(), NS(prompt_token_count=None, candidates_token_count=None), {}])
def test_no_counts_at_all_is_none(um):
    assert llm_usage.tokens_from_usage(um) is None


def test_tokens_from_usage_accepts_dicts_and_never_raises():
    got = llm_usage.tokens_from_usage({"prompt_token_count": 5, "candidates_token_count": 2})
    assert got["in_text"] == 5 and got["out_text"] == 2
    assert llm_usage.tokens_from_usage(NS(prompt_token_count="garbage")) is None


# ── cost ─────────────────────────────────────────────────────────────────────
def test_simple_price():
    assert llm_usage.cost(PRICES, "gemini", "m-simple", toks(in_text=M, out_text=M // 2)) == (3.0, False)


def test_per_modality_price():
    t = toks(in_text=M, in_audio=M, in_image=M, out_text=M, out_audio=M)
    assert llm_usage.cost(PRICES, "gemini", "m-live", t) == (pytest.approx(0.75 + 3 + 1 + 4.5 + 12), False)


def test_thoughts_are_billed_as_text_output():
    assert llm_usage.cost(PRICES, "gemini", "m-simple", toks(thoughts=M)) == (4.0, False)
    assert llm_usage.cost(PRICES, "gemini", "m-live", toks(thoughts=M)) == (4.5, False)


def test_cached_input_has_its_own_price_when_the_table_has_one():
    got, missing = llm_usage.cost(PRICES, "gemini", "m-cache", toks(in_text=M, in_cached=400_000))
    assert not missing and got == pytest.approx(600_000 * 0.30 / M + 400_000 * 0.03 / M)
    # No cached price: everything is billed at the normal input price.
    assert llm_usage.cost(PRICES, "gemini", "m-simple", toks(in_text=M, in_cached=400_000)) == (1.0, False)


def test_models_prefix_is_normalised():
    assert llm_usage.cost(PRICES, "gemini", "models/m-simple", toks(in_text=M)) == (1.0, False)


def test_unknown_model_and_unknown_tokens():
    assert llm_usage.cost(PRICES, "gemini", "nope", toks(in_text=5)) == (None, True)
    assert llm_usage.cost(PRICES, "gemini", "nope", None) == (None, True)
    assert llm_usage.cost(PRICES, "gemini", "m-simple", None) == (None, False)
    assert llm_usage.cost(PRICES, "openai", "m-simple", toks(in_text=5)) == (None, True)   # provider is part of the key


def test_peak_multiplier_only_inside_the_peak_hours_utc():
    t = toks(in_text=M)
    base, _ = llm_usage.cost(PRICES, "deepseek", "m-peak", t)
    assert base == pytest.approx(0.15)
    assert llm_usage.cost(PRICES, "deepseek", "m-peak", t, datetime(2026, 10, 17, 2, 0, tzinfo=UTC))[0] == pytest.approx(0.30)
    assert llm_usage.cost(PRICES, "deepseek", "m-peak", t, datetime(2026, 10, 17, 8, 0, tzinfo=UTC))[0] == pytest.approx(0.30)
    assert llm_usage.cost(PRICES, "deepseek", "m-peak", t, datetime(2026, 10, 17, 12, 0, tzinfo=UTC))[0] == pytest.approx(0.15)
    # 06:00 Brasilia is 09:00 UTC: peak.
    assert llm_usage.cost(PRICES, "deepseek", "m-peak", t, datetime(2026, 10, 17, 6, 0, tzinfo=BRT))[0] == pytest.approx(0.30)


def test_price_schedule_switches_on_its_date():
    t = toks(in_text=M, out_text=M)
    assert llm_usage.cost(PRICES, "gemini", "m-sched", t, datetime(2026, 12, 31, 23, 0, tzinfo=UTC))[0] == pytest.approx(4.5)
    assert llm_usage.cost(PRICES, "gemini", "m-sched", t, datetime(2027, 1, 2, 12, 0, tzinfo=UTC))[0] == pytest.approx(9.0)


def test_cost_never_raises_on_a_broken_table():
    assert llm_usage.cost({}, "gemini", "x", toks(in_text=1)) == (None, True)
    assert llm_usage.cost(None, "gemini", "x", toks(in_text=1)) == (None, True)
    assert llm_usage.cost({"models": {"gemini/x": "oops"}}, "gemini", "x", toks(in_text=1)) == (None, True)


# ── load_prices ──────────────────────────────────────────────────────────────
def test_load_prices_ignores_invalid_entries_without_raising(tmp_path, capsys):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"v": 1, "checked": "2026-10-16", "source": ["s"], "models": {
        "gemini/good": {"in": 1.0, "out": 2.0},
        "gemini/negative": {"in": -1.0, "out": 2.0},
        "gemini/string": {"in": "cheap", "out": 2.0},
        "gemini/no-out": {"in": 1.0},
        "gemini/bad-modality": {"in": {"text": "x"}, "out": 1.0},
        "nokey": {"in": 1, "out": 1},
        "gemini/not-a-dict": 5,
        "gemini/bad-peak": {"in": 1.0, "out": 1.0, "peak_multiplier": 0},
        "gemini/ok-modal": {"in": {"text": 1.0, "audio": 2.0}, "out": 3},
    }}), encoding="utf-8")
    got = llm_usage.load_prices(path)
    assert set(got["models"]) == {"gemini/good", "gemini/ok-modal"}
    assert got["checked"] == "2026-10-16"
    assert "gemini/negative" in capsys.readouterr().out          # skipped entries are named, not silent


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2]", '{"models": 3}'])
def test_load_prices_survives_missing_or_corrupt_files(tmp_path, content):
    path = tmp_path / "p.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    got = llm_usage.load_prices(path)
    assert got["models"] == {} and got["checked"] is None


def test_load_prices_is_cached_by_mtime(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"models": {"gemini/a": {"in": 1, "out": 1}}}), encoding="utf-8")
    first = llm_usage.load_prices(path)
    assert llm_usage.load_prices(path) is first
    path.write_text(json.dumps({"models": {"gemini/b": {"in": 1, "out": 1}}}), encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))
    assert set(llm_usage.load_prices(path)["models"]) == {"gemini/b"}


def test_the_shipped_price_table_is_valid_and_dated():
    path = llm_usage.PRICES_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    got = llm_usage.load_prices(path)
    assert len(got["models"]) == len(raw["models"]) > 10         # nothing was silently dropped
    assert got["checked"] and got["source"]
    assert "gemini/gemini-3.1-flash-live-preview" in got["models"]
    live = got["models"]["gemini/gemini-3.1-flash-live-preview"]
    assert live["in"]["audio"] == 3.0 and live["out"]["audio"] == 12.0


# ── LiveUsageMeter (rule: research R7, provisional) ──────────────────────────
def um(prompt, resp):
    return NS(prompt_token_count=prompt, response_token_count=resp)


def test_meter_emits_the_last_usage_seen_before_turn_complete():
    m = llm_usage.LiveUsageMeter()
    m.feed(um(10, 1))
    m.feed(um(20, 5))
    m.feed(um(30, 9))
    assert m.turn_complete() == toks(in_text=30, out_text=9, total=39)
    assert m.turn_complete() is None                 # consumed: the next turn starts empty


def test_meter_turn_complete_without_usage_is_none():
    m = llm_usage.LiveUsageMeter()
    assert m.turn_complete() is None
    m.feed(None)
    assert m.turn_complete() is None


def test_meter_turns_are_independent():
    m = llm_usage.LiveUsageMeter()
    m.feed(um(10, 1))
    assert m.turn_complete()["in_text"] == 10
    m.feed(um(40, 2))
    assert m.turn_complete()["in_text"] == 40


# ── summarize ────────────────────────────────────────────────────────────────
def rec(ts, model="gemini-2.5-flash", kind="oneshot", result="ok", tokens=None, cost=None,
        missing=False, provider="gemini", **kw):
    r = {"kind": kind, "ts": ts, "provider": provider, "model": model, "result": result,
         "tokens": tokens, "cost_usd_if_paid": cost, "cost_usd_actual": 0.0,
         "price_missing": missing, "billing": "free"}
    r.update(kw)
    return r


def test_summary_per_day_provider_model():
    records = [
        rec("2026-10-15T10:00:00-03:00", tokens=toks(in_text=100, out_text=10, total=110), cost=0.01),
        rec("2026-10-15T11:00:00-03:00", tokens=toks(in_text=200, out_text=20, total=220), cost=0.02),
        rec("2026-10-15T14:05:00-03:00", result="quota"),
        rec("2026-10-15T16:30:00-03:00", result="quota"),
        rec("2026-10-16T09:00:00-03:00", tokens=toks(in_text=5, out_text=1, total=6), cost=0.001),
        rec("2026-10-15T12:00:00-03:00", model="gemini-3.1-flash-live-preview", kind="live_turn",
            tokens=toks(in_text=1000, in_audio=2000, out_audio=500, total=3500), cost=0.5),
        {"kind": "live_session", "event": "end", "ts": "2026-10-15T12:30:00-03:00", "provider": "gemini",
         "model": "gemini-3.1-flash-live-preview", "duration_s": 1800, "turns": 40, "end_reason": "voluntary"},
        {"kind": "live_session", "event": "start", "ts": "2026-10-15T12:00:00-03:00", "provider": "gemini",
         "model": "gemini-3.1-flash-live-preview"},
    ]
    s = llm_usage.summarize(records, BRT)
    rows = {(r["day"], r["model"]): r for r in s["rows"]}
    flash = rows[("2026-10-15", "gemini-2.5-flash")]
    assert flash["provider"] == "gemini"
    assert (flash["calls"], flash["ok"], flash["quota_errors"]) == (4, 2, 2)
    assert flash["quota_hours"] == [14, 16]
    assert flash["tokens"]["in_text"] == 300 and flash["tokens"]["out_text"] == 30
    assert flash["cost_usd_if_paid"] == pytest.approx(0.03)
    assert flash["calls_with_tokens"] == 2
    assert rows[("2026-10-16", "gemini-2.5-flash")]["calls"] == 1
    live = rows[("2026-10-15", "gemini-3.1-flash-live-preview")]
    assert live["calls"] == 1 and live["voice_minutes"] == pytest.approx(30.0)   # session records are not calls
    assert live["tokens"]["in_audio"] == 2000
    assert s["totals"]["calls"] == 6 and s["totals"]["ok"] == 4 and s["totals"]["quota_errors"] == 2
    assert s["totals"]["cost_usd_if_paid"] == pytest.approx(0.531)
    assert s["totals"]["cost_usd_actual"] == 0.0
    assert s["totals"]["voice_minutes"] == pytest.approx(30.0)
    assert s["models_without_price"] == {}


def test_summary_rows_are_sorted_by_day_then_model():
    s = llm_usage.summarize([rec("2026-10-16T09:00:00-03:00", model="b"), rec("2026-10-15T09:00:00-03:00", model="z"),
                             rec("2026-10-15T09:00:00-03:00", model="a")], BRT)
    assert [(r["day"], r["model"]) for r in s["rows"]] == [("2026-10-15", "a"), ("2026-10-15", "z"), ("2026-10-16", "b")]


def test_unknown_costs_are_counted_not_zeroed():
    records = [rec("2026-10-15T10:00:00-03:00", model="mystery", missing=True, tokens=toks(in_text=5)),
               rec("2026-10-15T10:01:00-03:00", model="mystery", missing=True, tokens=toks(in_text=5)),
               rec("2026-10-15T10:02:00-03:00", model="priced", tokens=None),           # no token counts
               rec("2026-10-15T10:03:00-03:00", model="priced", tokens=toks(in_text=1), cost=0.5)]
    s = llm_usage.summarize(records, BRT)
    assert s["totals"]["cost_unknown_calls"] == 3
    assert s["totals"]["cost_usd_if_paid"] == pytest.approx(0.5)
    assert s["models_without_price"] == {"mystery": 2}
    assert s["totals"]["calls_with_tokens"] == 3


@pytest.mark.parametrize("ts, day", [
    ("2026-10-16T23:59:30-03:00", "2026-10-16"),
    ("2026-10-17T00:00:10-03:00", "2026-10-17"),
    ("2026-10-17T02:30:00+00:00", "2026-10-16"),       # 23:30 in Brasilia
    ("2026-10-17T03:00:00+00:00", "2026-10-17"),       # 00:00 in Brasilia
])
def test_day_is_the_local_day_of_each_record(ts, day):
    (row,) = llm_usage.summarize([rec(ts)], BRT)["rows"]
    assert row["day"] == day


def test_quota_events_carry_the_provider_quota_day():
    # 2026-10-17 01:30 Brasilia = 04:30Z = 21:30 Pacific on the 16th (PDT): provider day 10-16.
    # 2026-10-17 04:30 Brasilia = 07:30Z = 00:30 Pacific on the 17th: provider day 10-17.
    s = llm_usage.summarize([rec("2026-10-17T01:30:00-03:00", result="quota"),
                             rec("2026-10-17T04:30:00-03:00", result="quota")], BRT)
    ev = s["quota_events"]
    assert [(e["local_day"], e["hour"], e["provider_day"]) for e in ev] == [
        ("2026-10-17", 1, "2026-10-16"), ("2026-10-17", 4, "2026-10-17")]
    assert s["rows"][0]["quota_days"] == ["2026-10-16", "2026-10-17"]


def test_summary_skips_garbage_records():
    s = llm_usage.summarize([None, "x", {"kind": "oneshot"}, {"ts": "not a time", "kind": "oneshot"},
                             rec("2026-10-15T10:00:00-03:00")], BRT)
    assert s["totals"]["calls"] == 1 and s["skipped"] == 4


def test_summary_of_nothing():
    s = llm_usage.summarize([], BRT)
    assert s["rows"] == [] and s["totals"]["calls"] == 0 and s["models_without_price"] == {}


def test_summary_is_json_serialisable():
    s = llm_usage.summarize([rec("2026-10-15T10:00:00-03:00", result="quota", tokens=toks(in_text=1))], BRT)
    assert json.loads(json.dumps(s))["totals"]["quota_errors"] == 1


def test_pacific_day_across_dst_changes():
    f = errlog.pacific_day
    assert str(f(datetime(2026, 10, 17, 4, 30, tzinfo=UTC))) == "2026-10-16"
    assert str(f(datetime(2026, 10, 17, 7, 30, tzinfo=UTC))) == "2026-10-17"
    assert str(f(datetime(2026, 1, 15, 7, 59, tzinfo=UTC))) == "2026-01-14"     # PST: midnight is 08:00Z
    assert str(f(datetime(2026, 1, 15, 8, 0, tzinfo=UTC))) == "2026-01-15"
    assert str(f(datetime(2026, 3, 8, 8, 0, tzinfo=UTC))) == "2026-03-08"       # spring-forward day starts in PST


# ── records on disk (writer thread) ──────────────────────────────────────────
@pytest.fixture
def usage_on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES), encoding="utf-8")
    monkeypatch.setattr(llm_usage, "PRICES_PATH", prices)
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    errlog.setup(log_dir, tee_console=False)
    yield log_dir
    llm_usage._reset_for_tests()


def usage_lines(log_dir):
    files = sorted(log_dir.glob("usage-2*.jsonl"))
    out = []
    for f in files:
        out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def test_record_oneshot_ok_with_tokens(usage_on):
    um_ = NS(prompt_token_count=M, candidates_token_count=M // 2)
    llm_usage.record_oneshot("models/m-simple", "fast", "ok", 812, um_)
    errlog.flush()
    (r,) = usage_lines(usage_on)
    assert (r["kind"], r["provider"], r["model"], r["tier_requested"]) == ("oneshot", "gemini", "m-simple", "fast")
    assert (r["billing"], r["result"], r["latency_ms"]) == ("free", "ok", 812)
    assert r["tokens"]["in_text"] == M and r["tokens"]["out_text"] == M // 2
    assert r["cost_usd_if_paid"] == pytest.approx(3.0) and r["cost_usd_actual"] == 0.0
    assert r["price_missing"] is False and r["v"] == 1


def test_record_oneshot_failure_has_no_tokens_and_unknown_cost(usage_on):
    llm_usage.record_oneshot("m-simple", "smart", "quota", 40)
    llm_usage.record_oneshot("never-priced", "fast", "ok", 40, NS(prompt_token_count=3))
    llm_usage.record_oneshot("m-simple", "fast", "network", 40)
    errlog.flush()
    quota, unpriced, net = usage_lines(usage_on)
    assert quota["result"] == "quota" and quota["tokens"] is None and quota["cost_usd_if_paid"] is None
    assert quota["price_missing"] is False
    assert unpriced["price_missing"] is True and unpriced["cost_usd_if_paid"] is None
    assert unpriced["tokens"]["in_text"] == 3
    assert net["result"] == "error"                   # the enum has no "network"


def test_paid_billing_makes_actual_equal_the_estimate(usage_on, monkeypatch):
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "paid")
    llm_usage.record_oneshot("m-simple", "fast", "ok", 10, NS(prompt_token_count=M))
    errlog.flush()
    (r,) = usage_lines(usage_on)
    assert r["billing"] == "paid" and r["cost_usd_actual"] == pytest.approx(1.0) == r["cost_usd_if_paid"]


def test_record_tts(usage_on):
    llm_usage.record_tts("gemini-2.5-flash-preview-tts", "ok", 4100, NS(prompt_token_count=12))
    errlog.flush()
    (r,) = usage_lines(usage_on)
    assert r["kind"] == "tts" and r["tier_requested"] is None and r["model"] == "gemini-2.5-flash-preview-tts"


def test_result_of_error_text():
    f = llm_usage.result_of
    assert f("429 RESOURCE_EXHAUSTED") == "quota"
    assert f("404 NOT_FOUND model") == "gone"
    assert f("503 UNAVAILABLE") == "unavailable"
    assert f(TimeoutError("the read operation timed out")) == "timeout"
    assert f(ValueError("x")) == "error"
    assert f("quota") == "quota" and f("network") == "error"


def msg(usage=None, tc=False):
    return NS(usage_metadata=usage, server_content=NS(turn_complete=tc), data=b"")


def test_live_session_turns_and_end(usage_on):
    llm_usage.live_session_start("models/m-live", "Puck", False)
    llm_usage.live_message(msg())                                  # plain audio chunk: nothing to record
    llm_usage.live_message(msg(NS(prompt_token_count=100, response_token_count=10,
                                  response_tokens_details=[{"modality": "AUDIO", "token_count": 10}])))
    llm_usage.live_message(msg(NS(prompt_token_count=120, response_token_count=40,
                                  response_tokens_details=[{"modality": "AUDIO", "token_count": 30},
                                                           {"modality": "TEXT", "token_count": 10}]), tc=True))
    llm_usage.live_message(msg(tc=True))                           # turn without usage
    llm_usage.live_session_end("voluntary")
    errlog.flush()
    start, turn1, turn2, end = usage_lines(usage_on)
    assert (start["kind"], start["event"], start["model"], start["voice"], start["resumed"]) == \
        ("live_session", "start", "m-live", "Puck", False)
    assert turn1["kind"] == "live_turn" and turn1["model"] == "m-live" and turn1["result"] == "ok"
    assert turn1["tokens"]["in_text"] == 120 and turn1["tokens"]["out_audio"] == 30
    assert turn1["cost_usd_if_paid"] == pytest.approx((120 * 0.75 + 30 * 12 + 10 * 4.5) / M)
    assert turn2["tokens"] is None and turn2["cost_usd_if_paid"] is None
    assert end["event"] == "end" and end["turns"] == 2 and end["end_reason"] == "voluntary"
    assert end["duration_s"] >= 0
    assert start["live_session"] is None or isinstance(start["live_session"], str)


def test_session_end_without_a_start_is_silent(usage_on):
    llm_usage.live_session_end("error")
    errlog.flush()
    assert usage_lines(usage_on) == []


def test_resumed_session_flag(usage_on):
    llm_usage.live_session_start("m-live", "Puck", True)
    errlog.flush()
    assert usage_lines(usage_on)[0]["resumed"] is True


def test_live_message_does_only_attribute_reads_on_the_callers_thread(usage_on, monkeypatch):
    threads = []
    real_cost, real_tokens, real_prices = llm_usage.cost, llm_usage.tokens_from_usage, llm_usage.load_prices
    monkeypatch.setattr(llm_usage, "cost", lambda *a, **k: (threads.append(threading.current_thread().name), real_cost(*a, **k))[1])
    monkeypatch.setattr(llm_usage, "tokens_from_usage", lambda *a, **k: (threads.append(threading.current_thread().name), real_tokens(*a, **k))[1])
    monkeypatch.setattr(llm_usage, "load_prices", lambda *a, **k: (threads.append(threading.current_thread().name), real_prices(*a, **k))[1])
    llm_usage.live_session_start("m-live", "Puck", False)
    for _ in range(50):
        llm_usage.live_message(msg(NS(prompt_token_count=1, response_token_count=1), tc=True))
    assert threads == []                                          # nothing computed yet, before the writer ran
    errlog.flush()
    assert threads and set(threads) == {"errlog-writer"}


def test_live_message_never_raises():
    class Evil:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    llm_usage.live_message(Evil())
    llm_usage.live_message(None)
    llm_usage.live_message(NS())


def test_usage_functions_are_noops_before_setup():
    llm_usage.record_oneshot("m", "fast", "ok", 1)
    llm_usage.record_tts("m", "ok", 1)
    llm_usage.live_session_start("m", "v", False)
    llm_usage.live_message(msg(NS(prompt_token_count=1), tc=True))
    llm_usage.live_session_end("shutdown")


def test_raw_mode_logs_every_usage_message_with_turn_complete_flag(usage_on, monkeypatch):
    monkeypatch.setenv("JARVIS_USAGE_RAW", "1")
    llm_usage.live_session_start("m-live", "Puck", False)
    llm_usage.live_message(msg())                                  # nothing: not worth a line
    llm_usage.live_message(msg(NS(prompt_token_count=10, response_token_count=2)))
    llm_usage.live_message(msg(NS(prompt_token_count=12, response_token_count=5), tc=True))
    llm_usage.live_message(msg(tc=True))                           # turn_complete with no usage: still shown
    errlog.flush()
    lines = [json.loads(line) for line in (usage_on / "usage-raw.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(l["has_turn_complete"], l["usage"] is not None) for l in lines] == [
        (False, True), (True, True), (True, False)]
    assert lines[0]["usage"] == {"prompt_token_count": 10, "response_token_count": 2}
    assert lines[0]["ts"] and lines[0]["v"] == 1
    assert len(usage_lines(usage_on)) == 3                         # the normal records are written too


def test_raw_mode_is_off_by_default(usage_on, monkeypatch):
    monkeypatch.delenv("JARVIS_USAGE_RAW", raising=False)
    llm_usage.live_message(msg(NS(prompt_token_count=10), tc=True))
    errlog.flush()
    assert not (usage_on / "usage-raw.jsonl").exists()


def test_raw_mode_dumps_sdk_style_objects(usage_on, monkeypatch):
    monkeypatch.setenv("JARVIS_USAGE_RAW", "1")

    class FakePydantic:
        def model_dump(self, **kw):
            return {"prompt_token_count": 7, "prompt_tokens_details": [{"modality": "AUDIO", "token_count": 7}]}
    llm_usage.live_message(msg(FakePydantic()))
    errlog.flush()
    (line,) = [json.loads(l) for l in (usage_on / "usage-raw.jsonl").read_text(encoding="utf-8").splitlines()]
    assert line["usage"]["prompt_tokens_details"][0]["modality"] == "AUDIO"


def test_cost_of_live_message_on_the_callers_thread_is_tiny(usage_on):
    llm_usage.live_session_start("m-live", "Puck", False)
    m = msg(NS(prompt_token_count=5, response_token_count=5), tc=False)
    samples = []
    for _ in range(3000):
        t0 = time.perf_counter()
        llm_usage.live_message(m)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    assert samples[int(len(samples) * 0.95)] < 0.2                # ms; generous bound, real value is ~0.005


# ── voice sessions that never wrote their "end" (app closed by os._exit / Ctrl-C) ──────────
def session(sid, event, ts, **kw):
    r = {"kind": "live_session", "event": event, "ts": ts, "provider": "gemini",
         "model": "live-m", "live_session": sid}
    r.update(kw)
    return r


def turn(sid, ts):
    return rec(ts, model="live-m", kind="live_turn", live_session=sid,
               tokens=toks(in_text=10, out_audio=5, total=15), cost=0.01)


def test_session_with_an_end_record_uses_its_duration():
    s = llm_usage.summarize([
        session("aaa", "start", "2026-10-17T10:00:00-03:00"),
        turn("aaa", "2026-10-17T10:05:00-03:00"),
        session("aaa", "end", "2026-10-17T10:20:00-03:00", duration_s=1200, turns=1, end_reason="voluntary"),
    ], BRT)
    assert s["totals"]["voice_minutes"] == pytest.approx(20.0)
    assert s["totals"]["voice_minutes_estimated"] == 0 and s["totals"]["sessions_without_end"] == 0


def test_session_without_an_end_is_estimated_from_its_last_record():
    s = llm_usage.summarize([
        session("bbb", "start", "2026-10-17T22:28:10-03:00"),
        turn("bbb", "2026-10-17T22:29:47-03:00"),
        turn("bbb", "2026-10-17T22:31:53-03:00"),          # the last thing it did: 3 min 43 s after the start
    ], BRT)
    t = s["totals"]
    assert t["sessions_without_end"] == 1
    assert t["voice_minutes_estimated"] == pytest.approx(223 / 60, abs=0.01)
    assert t["voice_minutes"] == pytest.approx(223 / 60, abs=0.01)          # the estimate also counts as voice time
    (row,) = s["rows"]
    assert row["voice_minutes"] == pytest.approx(223 / 60, abs=0.01) and row["voice_minutes_estimated"] > 0
    assert row["sessions_without_end"] == 1 and row["calls"] == 2


def test_session_without_turns_is_zero_but_counted():
    s = llm_usage.summarize([session("ccc", "start", "2026-10-17T09:00:00-03:00")], BRT)
    assert s["totals"]["sessions_without_end"] == 1
    assert s["totals"]["voice_minutes"] == 0 and s["totals"]["voice_minutes_estimated"] == 0
    assert s["rows"] and s["rows"][0]["sessions_without_end"] == 1


def test_one_shot_calls_during_the_session_extend_the_estimate():
    s = llm_usage.summarize([
        session("ddd", "start", "2026-10-17T10:00:00-03:00"),
        rec("2026-10-17T10:07:00-03:00", live_session="ddd"),             # a oneshot made while the voice was open
    ], BRT)
    assert s["totals"]["voice_minutes_estimated"] == pytest.approx(7.0)


def test_each_session_is_judged_on_its_own_id():
    s = llm_usage.summarize([
        session("one", "start", "2026-10-17T10:00:00-03:00"),
        session("one", "end", "2026-10-17T10:10:00-03:00", duration_s=600),
        session("two", "start", "2026-10-17T11:00:00-03:00"),
        turn("two", "2026-10-17T11:02:00-03:00"),
        turn("one", "2026-10-17T11:30:00-03:00"),                          # stale id: "one" HAS an end, so it is ignored
    ], BRT)
    t = s["totals"]
    assert t["sessions_without_end"] == 1 and t["voice_minutes_estimated"] == pytest.approx(2.0)
    assert t["voice_minutes"] == pytest.approx(12.0)


def test_records_without_a_session_id_do_not_create_sessions():
    s = llm_usage.summarize([session(None, "start", "2026-10-17T10:00:00-03:00"), rec("2026-10-17T10:05:00-03:00")], BRT)
    assert s["totals"]["sessions_without_end"] == 0


def test_estimated_minutes_land_on_the_day_of_the_last_record():
    s = llm_usage.summarize([session("eee", "start", "2026-10-17T23:50:00-03:00"),
                             turn("eee", "2026-10-18T00:10:00-03:00")], BRT)
    (row,) = [r for r in s["rows"] if r["voice_minutes"]]
    assert row["day"] == "2026-10-18" and row["voice_minutes"] == pytest.approx(20.0)
