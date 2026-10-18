"""core/errlog.py — record_failure, note_tool, burst limit, notify_once (US1 behaviour on disk)."""
import json
import re
import sys
import time

import pytest

from core import errlog

GEMINI = "AIza" + "Sy" + "a" * 33


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    """errlog running into tmp_path with a throwaway secrets file."""
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    return log_dir / "failures.jsonl"


def _explode():
    raise ValueError("kaput")


def test_record_failure_with_exception_has_everything(on):
    try:
        _explode()
    except ValueError as e:
        errlog.record_failure("action", "demo", exc=e, phase="run",
                              context={"args": {"path": "x.txt", "api_key": "hunter2-hunter2"}})
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["kind"] == "exception" and rec["component"] == "action"
    assert rec["name"] == "demo" and rec["phase"] == "run"
    assert rec["error_type"] == "ValueError" and rec["message"] == "kaput"
    assert "ValueError: kaput" in rec["traceback"]
    assert rec["origin"] == "tests/test_errlog_failures.py:_explode"
    assert isinstance(rec["origin_line"], int)
    assert rec["signature"] == errlog.signature("action", "demo", "ValueError", rec["origin"])
    assert rec["context"]["args"]["path"] == "x.txt"
    assert rec["context"]["args"]["api_key"].startswith("‹redacted")
    assert "transcript" not in rec                      # automatic failures never carry the conversation
    assert "suppressed" not in rec


def test_record_failure_without_exception_uses_reason_and_caller_as_origin(on):
    errlog.record_failure("gemini", "gemini-2.5-flash", error_type="quota",
                          message="429 RESOURCE_EXHAUSTED", context={"tier": "fast"})
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["kind"] == "gemini" and rec["error_type"] == "quota"
    assert rec["traceback"] is None
    assert rec["origin"] == "tests/test_errlog_failures.py:test_record_failure_without_exception_uses_reason_and_caller_as_origin"
    assert rec["signature"] == errlog.signature("gemini", "gemini-2.5-flash", "quota", rec["origin"])


def test_explicit_origin_and_kinds_per_component(on):
    errlog.record_failure("live", "live_connect", error_type="net", origin="main.py:run")
    errlog.record_failure("plugin", "p", error_type="X", origin="plugins/p.py:run")
    errlog.flush()
    live, plugin = read_lines(on)
    assert live["kind"] == "live" and live["origin"] == "main.py:run" and live["origin_line"] is None
    assert plugin["kind"] == "exception"


def test_same_failure_from_different_lines_shares_one_signature(on):
    for i in range(5):
        try:
            _explode()
        except ValueError as e:
            errlog.record_failure("tool", "t", exc=e)
    errlog.flush()
    recs = read_lines(on)
    assert len({r["signature"] for r in recs}) == 1
    (g,) = errlog.group(recs)
    assert g["count"] == 5


def test_record_failure_never_raises(on):
    class Evil:
        def __str__(self):
            raise RuntimeError("no str for you")
    errlog.record_failure("action", "x", exc=Evil(), context={"o": Evil()})
    errlog.record_failure(None, None)
    errlog.flush()                                       # writer survived


# ── burst limit (SC-008) ─────────────────────────────────────────────────────
def _soft(ts):
    errlog._enqueue("failures", {"kind": "soft", "component": "tool", "name": "x",
                                 "error_type": "soft:could not save",
                                 "origin": "main.py:_execute_tool"}, ts=ts)


def test_burst_keeps_twenty_then_one_suppressed_summary_a_minute_later(on, monkeypatch):
    clock = {"t": 1_700_000_000.0}
    monkeypatch.setattr(errlog, "_clock", lambda: clock["t"])
    for i in range(25):
        _soft(1_700_000_000.0 + i * 0.01)
    errlog.flush()
    assert len(read_lines(on)) == 20                      # 5 held back, not yet written
    clock["t"] = 1_700_000_065.0                                   # a minute after the first one held back
    errlog._enqueue("failures", {"kind": "soft", "component": "tool", "name": "other",
                                 "error_type": "soft:x", "origin": "main.py:f"}, ts=1_700_000_065.0)
    errlog.flush()
    recs = read_lines(on)
    summaries = [r for r in recs if r.get("suppressed")]
    assert len(recs) == 22 and len(summaries) == 1
    assert summaries[0]["suppressed"] == 5 and summaries[0]["signature"] == recs[0]["signature"]
    (g,) = [g for g in errlog.group(recs) if g["name"] == "x"]
    assert g["count"] == 25                               # nothing is lost from the tally


def test_burst_summary_waits_a_minute_but_is_written_at_exit(on):
    now = time.time()
    for i in range(25):
        _soft(now)
    errlog.flush()
    assert len(read_lines(on)) == 20                      # the 5 held back are not written yet
    errlog.flush(final=True)                              # what atexit does
    recs = read_lines(on)
    assert len(recs) == 21 and recs[-1]["suppressed"] == 5


def test_distinct_signatures_are_not_limited_together(on):
    now = time.time()
    for i in range(30):
        errlog._enqueue("failures", {"kind": "soft", "component": "tool", "name": f"t{i}",
                                     "error_type": "soft:x", "origin": "main.py:f"}, ts=now)
    errlog.flush(final=True)
    assert len(read_lines(on)) == 30


def test_uncaught_records_get_signature_origin_and_burst_limit(on):
    for i in range(25):
        try:
            _explode()
        except ValueError:
            sys.excepthook(*sys.exc_info())
    errlog.flush(final=True)
    recs = read_lines(on)
    assert all(r["kind"] == "uncaught" for r in recs)
    assert recs[0]["origin"] == "tests/test_errlog_failures.py:_explode"
    assert recs[0]["signature"] == errlog.signature("app", "main", "ValueError", recs[0]["origin"])
    assert len(recs) == 21 and recs[-1]["suppressed"] == 5


# ── note_tool (research R1) ──────────────────────────────────────────────────
def test_note_tool_records_soft_failures_only(on):
    errlog.note_tool("open_file", {"path": "C:\\nope.txt"}, "Could not open: [Errno 2] C:\\nope.txt")
    errlog.note_tool("open_app", {"app": "excel"}, "Opened Excel")
    errlog.note_tool("broken", {}, "Tool 'broken' failed: boom")        # loader already recorded it
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["kind"] == "soft" and rec["component"] == "tool" and rec["name"] == "open_file"
    assert rec["error_type"] == "soft:could not open"
    assert rec["origin"] == "main.py:_execute_tool"
    assert rec["context"]["args"] == {"path": "C:\\nope.txt"}
    assert rec["context"]["result"].startswith("Could not open")
    assert rec["message"].startswith("Could not open")


def test_note_tool_ring_keeps_the_last_eight(on):
    for i in range(10):
        errlog.note_tool(f"tool{i}", {"i": i}, "Done." if i % 2 else "Could not do it")
    ring = errlog._recent_tools()
    assert [t["name"] for t in ring] == [f"tool{i}" for i in range(2, 10)]
    assert set(ring[0]) == {"ts", "name", "args", "result", "soft_failure"}
    assert ring[0]["soft_failure"] is True and ring[1]["soft_failure"] is False


def test_note_tool_clips_long_values_and_never_raises(on):
    errlog.note_tool("big", {"text": "x" * 5000}, "r" * 5000)
    errlog.note_tool(None, None, None)
    (entry, _) = errlog._recent_tools()[:2]
    assert len(entry["result"]) <= 600 and len(entry["args"]["text"]) <= 600


def test_note_tool_before_setup_is_harmless():
    errlog.note_tool("x", {}, "Could not do it")


# ── notify_once ──────────────────────────────────────────────────────────────
def test_notify_once_rate_limits_per_key(on):
    seen = []
    errlog.bind(notify=seen.append)
    errlog.notify_once("ladder", "first")
    errlog.notify_once("ladder", "second")                # inside the 300 s window
    errlog.notify_once("other", "third")                  # another key is independent
    assert seen == ["first", "third"]
    errlog.notify_once("ladder", "again", every_s=0)
    assert seen[-1] == "again"


def test_notify_once_without_screen_prints(on, capsys):
    errlog.notify_once("k", "SYS: hello console")
    assert "SYS: hello console" in capsys.readouterr().out


def test_notify_once_never_raises_when_notify_does(on):
    def bad(_):
        raise RuntimeError("ui gone")
    errlog.bind(notify=bad)
    errlog.notify_once("k", "x")


# ── ladder_message (research R12) ────────────────────────────────────────────
def test_ladder_message_for_exhausted_quota():
    msg = errlog.ladder_message({f"m{i}": "quota" for i in range(8)})
    assert re.fullmatch(r"SYS: Gemini free quota used up on 8 models — back at \d\d:\d\d\.", msg)


def test_ladder_message_when_models_are_not_answering():
    reasons = {"a": "unavailable", "b": "timeout", "c": "quota", "d": "gone"}
    assert errlog.ladder_message(reasons) == "SYS: Gemini models not answering (4 tried) — try again shortly."


def test_ladder_message_majority_rule():
    assert "quota used up on 3" in errlog.ladder_message({"a": "quota", "b": "quota", "c": "quota", "d": "timeout"})
    assert "not answering" in errlog.ladder_message({"a": "quota", "b": "timeout", "c": "timeout"})


# ── live session id and attempt counter (main.py's connection loop) ──────────
def test_new_live_session_stamps_records_and_resets_attempts(on):
    sid = errlog.new_live_session()
    assert re.fullmatch(r"[0-9a-f]{8}", sid)
    assert [errlog.next_live_attempt() for _ in range(3)] == [1, 2, 3]
    errlog.record_failure("live", "live_connect", error_type="net", origin="main.py:run",
                          context={"attempt": 3})
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["live_session"] == sid
    assert errlog.new_live_session() != sid
    assert errlog.next_live_attempt() == 1                # connecting resets the count
