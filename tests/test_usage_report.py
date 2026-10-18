"""tools/usage_report.py — the 14-day summary (contract: usage-report-cli.md). Synthetic records only."""
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest

from core import llm_usage
from tools import usage_report

NOW = datetime.now().astimezone().replace(microsecond=0)


def day_ts(days_ago, hour=10, minute=0):
    d = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0)
    return d.isoformat()


def rec(days_ago, model="gemini-2.5-flash", result="ok", hour=10, tokens=None, cost=0.01, kind="oneshot",
        missing=False, **kw):
    r = {"v": 1, "kind": kind, "ts": day_ts(days_ago, hour), "provider": "gemini", "model": model,
         "result": result, "billing": "free",
         "tokens": tokens if tokens is not None else {"in_text": 1000, "out_text": 100, "total": 1100},
         "cost_usd_if_paid": None if missing else cost, "cost_usd_actual": 0.0, "price_missing": missing}
    r.update(kw)
    return r


def write(logs, records, corrupt=()):
    logs.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for r in records:
        by_month.setdefault(r["ts"][:7], []).append(json.dumps(r))
    for month, lines in by_month.items():
        extra = list(corrupt) if month == NOW.strftime("%Y-%m") else []
        (logs / f"usage-{month}.jsonl").write_text("\n".join(lines + extra) + "\n", encoding="utf-8")


@pytest.fixture
def prices(tmp_path, monkeypatch):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps({"v": 1, "checked": "2026-10-16", "source": ["x"], "models": {}}), encoding="utf-8")
    monkeypatch.setattr(llm_usage, "PRICES_PATH", p)
    return p


def run(capsys, *argv):
    code = usage_report.main(list(argv))
    return code, capsys.readouterr().out


def test_table_with_period_header_and_price_date(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(1), rec(1), rec(2, model="gemini-3.1-flash-live-preview", kind="live_turn")])
    code, out = run(capsys, "--logs", str(logs), "--days", "14")
    assert code == 0
    assert "Uso de LLM" in out and "(14 dias)" in out
    assert "preços conferidos em 2026-10-16" in out
    assert "gemini-2.5-flash" in out and "gemini-3.1-flash-live-preview" in out
    assert "Total do período" in out and "nível grátis" in out


def test_default_period_is_fourteen_days(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(13), rec(20)])                       # 20 days ago is outside the default window
    code, out = run(capsys, "--logs", str(logs))
    assert code == 0 and "(14 dias)" in out
    code, js = run(capsys, "--logs", str(logs), "--json")
    assert json.loads(js)["totals"]["calls"] == 1


def test_json_matches_summarize(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    records = [rec(1), rec(1, result="quota", hour=14, tokens={}), rec(3, model="other", missing=True)]
    write(logs, records)
    code, out = run(capsys, "--logs", str(logs), "--days", "14", "--json")
    assert code == 0
    got = json.loads(out)
    expected = json.loads(json.dumps(llm_usage.summarize(records, None)))
    for key in ("rows", "totals", "models_without_price", "quota_events"):
        assert got[key] == expected[key], key
    assert got["period"]["days"] == 14
    assert got["prices_checked"] == "2026-10-16"


def test_no_records_in_the_period(tmp_path, capsys, prices):
    code, out = run(capsys, "--logs", str(tmp_path / "does-not-exist"), "--days", "14")
    assert code == 0 and "nenhum registro no período" in out
    logs = tmp_path / "logs"
    write(logs, [rec(100)])
    code, out = run(capsys, "--logs", str(logs), "--days", "14")
    assert code == 0 and "nenhum registro no período" in out


def test_no_records_with_json_still_prints_valid_json(tmp_path, capsys, prices):
    code, out = run(capsys, "--logs", str(tmp_path / "none"), "--json")
    assert code == 0 and json.loads(out)["totals"]["calls"] == 0


@pytest.mark.parametrize("argv", [
    ["--days", "0"], ["--days", "abc"], ["--days", "-3"],
    ["--from", "2026-13-45", "--to", "2026-10-16"],
    ["--from", "2026-10-16", "--to", "2026-10-01"],
    ["--to", "2026-10-16"],
    ["--days", "5", "--from", "2026-10-01"],
    ["--by", "color"], ["--nonsense"],
])
def test_invalid_arguments_exit_with_2(tmp_path, capsys, prices, argv):
    code, _ = run(capsys, "--logs", str(tmp_path), *argv)
    assert code == 2


def test_explicit_period(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(2), rec(4), rec(9)])
    start = (NOW - timedelta(days=5)).strftime("%Y-%m-%d")
    end = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    code, out = run(capsys, "--logs", str(logs), "--from", start, "--to", end, "--json")
    assert code == 0 and json.loads(out)["totals"]["calls"] == 2


def test_corrupt_lines_are_ignored_and_counted(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(1), rec(2)], corrupt=['{"kind": "oneshot", "ts": "2026-', "not json at all"])
    code, out = run(capsys, "--logs", str(logs))
    assert code == 0
    assert "2 linha(s) corrompida(s) ignorada(s)" in out
    code, js = run(capsys, "--logs", str(logs), "--json")
    assert json.loads(js)["totals"]["calls"] == 2 and json.loads(js)["corrupt_lines"] == 2


def test_quota_and_missing_price_footers(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(1, result="quota", hour=14, tokens={}), rec(1, result="quota", hour=16, tokens={}),
                 rec(1, model="mystery", missing=True), rec(1, model="mystery", missing=True)])
    code, out = run(capsys, "--logs", str(logs))
    assert "Cota grátis esgotou" in out and "14h" in out and "16h" in out
    assert "Modelos sem preço na tabela" in out and "mystery (2 chamadas)" in out


def test_grouping_by_model_and_provider(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(1), rec(2), rec(2, model="gemini-2.5-flash-lite")])
    code, by_model = run(capsys, "--logs", str(logs), "--by", "model")
    assert code == 0 and by_model.count("gemini-2.5-flash-lite") >= 1
    header = [l for l in by_model.splitlines() if l.startswith("modelo") or " modelo " in l][0]
    assert "dia" not in header.split()
    code, by_provider = run(capsys, "--logs", str(logs), "--by", "provider")
    assert code == 0 and "gemini" in by_provider


def test_voice_minutes_and_token_formatting(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    write(logs, [
        rec(1, model="live", kind="live_turn", tokens={"in_text": 1_840_000, "in_audio": 0, "out_audio": 96_000, "total": 1_936_000}),
        {"v": 1, "kind": "live_session", "event": "end", "ts": day_ts(1, 11), "provider": "gemini",
         "model": "live", "duration_s": 2280, "turns": 12, "end_reason": "voluntary"}])
    code, out = run(capsys, "--logs", str(logs))
    line = [l for l in out.splitlines() if " live " in l][0]
    assert "38" in line and "1.84M" in line and "96k" in line


def test_fourteen_days_of_600_records_a_day_run_fast(tmp_path, capsys, prices):
    logs = tmp_path / "logs"
    records = [rec(d, model=f"m{i % 4}", hour=i % 24) for d in range(14) for i in range(600)]
    write(logs, records)
    t0 = time.perf_counter()
    code, out = run(capsys, "--logs", str(logs), "--days", "14")
    elapsed = time.perf_counter() - t0
    assert code == 0 and elapsed < 5.0, elapsed


def test_runs_as_a_script_in_a_plain_console(tmp_path, prices):
    logs = tmp_path / "logs"
    write(logs, [rec(1)])
    out = subprocess.run([sys.executable, str(usage_report.__file__), "--logs", str(logs)],
                         capture_output=True, text=True, encoding="cp1252", errors="strict")
    assert out.returncode == 0, out.stderr
    assert "Uso de LLM" in out.stdout
