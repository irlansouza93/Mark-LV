"""core/errlog.py — queue + writer thread: records reach disk, filtered, bounded, never raising."""
import io
import json
import os
import re
import sys
import threading
import time
from datetime import date, datetime

import pytest

from core import errlog

GEMINI = "AIza" + "Sy" + "a" * 33


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point the config-secrets reader at a throwaway api_keys.json (never the real one)."""
    path = tmp_path / "api_keys.json"
    path.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", path)
    return path


def test_record_reaches_disk_with_common_fields(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {"kind": "exception", "message": "boom"})
    errlog.flush()
    (rec,) = read_lines(log_dir / "failures.jsonl")
    assert rec["v"] == 1
    assert datetime.fromisoformat(rec["ts"]).tzinfo is not None
    assert rec["app"] == errlog.version()
    assert rec["pid"] == os.getpid()
    assert rec["live_session"] is None
    assert rec["kind"] == "exception" and rec["message"] == "boom"
    assert "dropped" not in rec


def test_live_session_id_is_stamped(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog.set_live_session("abc12345")
    errlog._enqueue("failures", {"kind": "live"})
    errlog.set_live_session(None)
    errlog._enqueue("failures", {"kind": "live"})
    errlog.flush()
    first, second = read_lines(log_dir / "failures.jsonl")
    assert first["live_session"] == "abc12345" and second["live_session"] is None


def test_streams_are_routed_to_their_own_files(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {"kind": "soft"})
    errlog._enqueue("reports", {"kind": "report", "expected": "x"})
    errlog._enqueue("usage", {"kind": "oneshot", "model": "m"})
    errlog.flush()
    month = date.today().strftime("%Y-%m")
    assert [r["kind"] for r in read_lines(log_dir / "failures.jsonl")] == ["soft"]
    assert [r["kind"] for r in read_lines(log_dir / "reports.jsonl")] == ["report"]
    assert [r["kind"] for r in read_lines(log_dir / f"usage-{month}.jsonl")] == ["oneshot"]


def test_usage_goes_to_the_month_of_the_event(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    ts = datetime(2026, 9, 30, 23, 59).astimezone().timestamp()
    errlog._enqueue("usage", {"kind": "oneshot"}, ts=ts)
    errlog.flush()
    (rec,) = read_lines(log_dir / "usage-2026-09.jsonl")
    assert rec["ts"].startswith("2026-09-30T23:59")


def test_unknown_stream_and_garbage_never_raise(log_dir, cfg, capsys):
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("nope", {"a": 1})
    errlog._enqueue("failures", None)
    errlog._enqueue("failures", "just a string")
    errlog.flush()          # still alive and answering


def test_nothing_happens_before_setup():
    errlog._enqueue("failures", {"kind": "x"})     # no writer yet: silent no-op, no exception
    errlog.flush()
    assert errlog.version()


def test_secrets_are_masked_in_every_field(log_dir, cfg):
    secret = "unit-test-secret-value-0001"                 # lives in the fake api_keys.json
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {
        "kind": "exception",
        "message": f"failed with {secret} and {GEMINI}",
        "context": {"args": {"q": f"use {secret}", "api_key": "plain"}},
        "traceback": f"Traceback...\nValueError: {secret}\n",
    })
    errlog.flush()
    raw = (log_dir / "failures.jsonl").read_text(encoding="utf-8")
    assert secret not in raw and GEMINI not in raw
    assert "‹redacted:" in raw


def test_config_secrets_are_reloaded_when_the_file_changes(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {"message": "first"})
    errlog.flush()
    new_secret = "another-brand-new-secret-9999"
    cfg.write_text(json.dumps({"k": new_secret}), encoding="utf-8")
    later = time.time() + 5
    os.utime(cfg, (later, later))                            # mtime moves even on coarse clocks
    errlog._enqueue("failures", {"message": f"leaks {new_secret}"})
    errlog.flush()
    assert new_secret not in (log_dir / "failures.jsonl").read_text(encoding="utf-8")


def test_free_text_is_truncated_with_a_counter(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {
        "message": "m" * 600,
        "context": {"result": "r" * 501, "ok": "short"},
        "traceback": "t" * 9000,
        "transcript": ["u" * 700],
    })
    errlog.flush()
    (rec,) = read_lines(log_dir / "failures.jsonl")
    assert rec["message"] == "m" * 500 + "…[+100 chars]"
    assert rec["context"]["result"] == "r" * 500 + "…[+1 chars]"
    assert rec["context"]["ok"] == "short"
    assert rec["traceback"] == "t" * 8000 + "…[+1000 chars]"
    assert rec["transcript"] == ["u" * 500 + "…[+200 chars]"]


def test_secret_is_masked_before_truncation(log_dir, cfg):
    # A secret straddling the 500-char cut must not survive as a readable prefix.
    secret = "unit-test-secret-value-0001"
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {"message": "x" * 490 + secret + "y" * 50})
    errlog.flush()
    raw = (log_dir / "failures.jsonl").read_text(encoding="utf-8")
    assert "unit-test-secret" not in raw


def test_exception_object_is_formatted_in_the_writer(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    try:
        raise ValueError("kaput")
    except ValueError as e:
        errlog._enqueue("failures", {"kind": "exception", "exc": e})
    errlog.flush()
    (rec,) = read_lines(log_dir / "failures.jsonl")
    assert "ValueError: kaput" in rec["traceback"]
    assert rec["error_type"] == "ValueError" and rec["message"] == "kaput"
    assert "exc" not in rec


def test_unserializable_data_becomes_text(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    loop = {}
    loop["me"] = loop
    errlog._enqueue("failures", {
        "kind": "soft",
        "context": {"obj": object(), "set": {1, 2}, 7: "int key", "loop": loop,
                    "raw": b"\xff\xfe", "nan": float("nan")},
    })
    errlog._enqueue("failures", {"kind": "after"})
    errlog.flush()
    recs = read_lines(log_dir / "failures.jsonl")
    assert [r["kind"] for r in recs] == ["soft", "after"]
    ctx = recs[0]["context"]
    assert isinstance(ctx["obj"], str) and "object object" in ctx["obj"]
    assert ctx["7"] == "int key"


def test_setup_twice_does_not_duplicate_anything(log_dir, cfg):
    errlog.setup(log_dir, tee_console=False)
    errlog.setup(log_dir, tee_console=False)
    errlog._enqueue("failures", {"kind": "once"})
    errlog.flush()
    assert len(read_lines(log_dir / "failures.jsonl")) == 1
    writers = [t for t in threading.enumerate() if t.name == "errlog-writer"]
    assert len(writers) == 1


def test_setup_with_another_dir_moves_the_writer(tmp_path, cfg):
    first, second = tmp_path / "a", tmp_path / "b"
    errlog.setup(first, tee_console=False)
    errlog._enqueue("failures", {"kind": "one"})
    errlog.setup(second, tee_console=False)
    errlog._enqueue("failures", {"kind": "two"})
    errlog.flush()
    assert [r["kind"] for r in read_lines(first / "failures.jsonl")] == ["one"]
    assert [r["kind"] for r in read_lines(second / "failures.jsonl")] == ["two"]
    assert len([t for t in threading.enumerate() if t.name == "errlog-writer"]) == 1


def test_unusable_log_dir_does_not_raise_and_warns_once(tmp_path, cfg, capsys):
    blocker = tmp_path / "afile"
    blocker.write_text("i am a file, not a folder")
    bad = blocker / "logs"                                   # mkdir under a regular file fails
    seen = []
    errlog.setup(bad, tee_console=False)
    errlog.bind(notify=seen.append)
    for _ in range(3):
        errlog._enqueue("failures", {"kind": "exception"})
    errlog.flush()
    err = capsys.readouterr().err
    assert err.count("log writing failed") == 1
    assert len(seen) == 1 and seen[0].startswith("SYS:")


def test_disk_full_style_failure_warns_once_and_keeps_going(log_dir, cfg, capsys, monkeypatch):
    errlog.setup(log_dir, tee_console=False)
    seen = []
    errlog.bind(notify=seen.append)

    def boom(self):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as m:
        m.setattr(errlog._Rotating, "_open", boom)
        for _ in range(4):
            errlog._enqueue("failures", {"kind": "exception"})
        errlog.flush()
    assert capsys.readouterr().err.count("log writing failed") == 1
    assert len(seen) == 1
    errlog._enqueue("failures", {"kind": "recovered"})        # the writer survived
    errlog.flush()
    kinds = [r["kind"] for r in read_lines(log_dir / "failures.jsonl")]
    assert kinds == ["recovered"]


def test_full_queue_drops_and_the_next_record_reports_it(log_dir, cfg, monkeypatch):
    errlog.setup(log_dir, tee_console=False)
    monkeypatch.setattr(errlog, "_MAX_QUEUE", 5)
    entered, release = threading.Event(), threading.Event()
    real_emit = errlog._Rotating.emit

    def slow_emit(self, record):
        entered.set()
        release.wait(5)
        return real_emit(self, record)
    monkeypatch.setattr(errlog._Rotating, "emit", slow_emit)

    errlog._enqueue("failures", {"kind": "r0"})
    assert entered.wait(5)                                   # writer is now stuck inside emit
    for i in range(1, 9):
        errlog._enqueue("failures", {"kind": f"r{i}"})       # 5 fit, 3 are dropped
    release.set()
    monkeypatch.setattr(errlog._Rotating, "emit", real_emit)
    errlog.flush(5)
    recs = read_lines(log_dir / "failures.jsonl")
    assert [r["kind"] for r in recs] == ["r0", "r1", "r2", "r3", "r4", "r5"]
    assert recs[1]["dropped"] == 3
    assert sum("dropped" in r for r in recs) == 1


def test_rotation_limits_follow_the_research_table():
    assert errlog._LIMITS["failures"] == (5 * 1024 * 1024, 4)
    assert errlog._LIMITS["reports"] == (1 * 1024 * 1024, 2)
    assert errlog._LIMITS["jarvis"] == (2 * 1024 * 1024, 5)
    assert errlog._LIMITS["usage"][0] == 0                   # monthly files, never rotated by size


def test_failures_file_rotates(log_dir, cfg, monkeypatch):
    monkeypatch.setitem(errlog._LIMITS, "failures", (1500, 2))
    errlog.setup(log_dir, tee_console=False)
    for i in range(40):
        errlog._enqueue("failures", {"kind": "soft", "message": f"line {i} " + "x" * 60})
    errlog.flush()
    assert (log_dir / "failures.jsonl.1").exists()
    assert not (log_dir / "failures.jsonl.3").exists()


def test_setup_deletes_usage_files_older_than_twelve_months(log_dir, cfg):
    log_dir.mkdir()
    today = date.today()
    old_idx = today.year * 12 + (today.month - 1) - 14
    old = f"usage-{old_idx // 12}-{old_idx % 12 + 1:02d}.jsonl"
    recent = f"usage-{today:%Y-%m}.jsonl"
    for name in (old, recent, "usage-raw.jsonl", "failures.jsonl"):
        (log_dir / name).write_text("{}\n")
    errlog.setup(log_dir, tee_console=False)
    assert not (log_dir / old).exists()
    for name in (recent, "usage-raw.jsonl", "failures.jsonl"):
        assert (log_dir / name).exists()


def test_flush_returns_when_the_writer_is_stuck(log_dir, cfg, monkeypatch):
    errlog.setup(log_dir, tee_console=False)
    release = threading.Event()
    real_emit = errlog._Rotating.emit
    monkeypatch.setattr(errlog._Rotating, "emit", lambda self, r: (release.wait(5), real_emit(self, r)))
    errlog._enqueue("failures", {"kind": "x"})
    t0 = time.monotonic()
    errlog.flush(0.2)
    assert time.monotonic() - t0 < 2
    release.set()
    monkeypatch.setattr(errlog._Rotating, "emit", real_emit)


def test_dead_writer_makes_records_a_noop_with_one_warning(log_dir, cfg, capsys):
    errlog.setup(log_dir, tee_console=False)
    errlog._stop_writer_for_test()
    for _ in range(3):
        errlog._enqueue("failures", {"kind": "x"})            # must not raise or pile up
    assert capsys.readouterr().err.count("writer thread is not running") == 1


# ── T009: console tee (research R3) and uncaught-exception hooks ─────────────
class _FakeConsole(io.StringIO):
    """A console stand-in that can refuse text like a cp1252 Windows console does."""
    encoding = "cp1252"

    def __init__(self, fail=None):
        super().__init__()
        self.fail = fail

    def write(self, s):
        if self.fail is not None:
            raise self.fail
        return super().write(s)


def install_consoles(monkeypatch):
    """Swap in fake consoles. Called inside the test body: pytest's own capture re-points
    sys.stdout when the call phase starts, which would undo a fixture."""
    out, err = _FakeConsole(), _FakeConsole()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err


def test_tee_mirrors_console_to_the_original_and_to_the_log(log_dir, cfg, monkeypatch):
    out, err = install_consoles(monkeypatch)
    errlog.setup(log_dir, tee_console=True)
    print("hello world")
    print(f"oops {GEMINI}", file=sys.stderr)
    errlog.flush()
    assert out.getvalue() == "hello world\n"                    # byte for byte what print wrote
    assert err.getvalue() == f"oops {GEMINI}\n"                 # the console itself is NOT filtered
    log = (log_dir / "jarvis.log").read_text(encoding="utf-8")
    assert "OUT hello world" in log and "ERR oops" in log
    assert GEMINI not in log                                    # the file is
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} OUT ", log)


def test_tee_joins_partial_writes_into_lines(log_dir, cfg, monkeypatch):
    install_consoles(monkeypatch)
    errlog.setup(log_dir, tee_console=True)
    sys.stdout.write("part")
    sys.stdout.write("ial line\nsecond\n\n")
    errlog.flush()
    lines = (log_dir / "jarvis.log").read_text(encoding="utf-8").splitlines()
    assert [l.split(" ", 3)[3] for l in lines] == ["partial line", "second"]


def test_tee_never_raises_when_the_console_cannot_encode(log_dir, cfg, monkeypatch):
    bad = _FakeConsole(fail=UnicodeEncodeError("charmap", "x", 0, 1, "cannot encode"))
    monkeypatch.setattr(sys, "stdout", bad)
    monkeypatch.setattr(sys, "stderr", _FakeConsole())
    errlog.setup(log_dir, tee_console=True)
    print("emoji \U0001F916 on a legacy code page")               # must not propagate
    sys.stdout.flush()
    errlog.flush()
    assert "emoji" in (log_dir / "jarvis.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("failure", [OSError("closed"), ValueError("I/O operation on closed file")])
def test_tee_swallows_any_console_failure(log_dir, cfg, monkeypatch, failure):
    monkeypatch.setattr(sys, "stdout", _FakeConsole(fail=failure))
    monkeypatch.setattr(sys, "stderr", _FakeConsole())
    errlog.setup(log_dir, tee_console=True)
    print("still logged")
    errlog.flush()
    assert "still logged" in (log_dir / "jarvis.log").read_text(encoding="utf-8")


def test_tee_works_without_a_console(log_dir, cfg, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)                    # pythonw.exe has no stdout
    monkeypatch.setattr(sys, "stderr", None)
    errlog.setup(log_dir, tee_console=True)
    sys.stdout.write("headless line\n")
    errlog.flush()
    assert "headless line" in (log_dir / "jarvis.log").read_text(encoding="utf-8")


def test_tee_delegates_stream_attributes(log_dir, cfg, monkeypatch):
    install_consoles(monkeypatch)
    errlog.setup(log_dir, tee_console=True)
    assert sys.stdout.encoding == "cp1252"
    assert sys.stdout.isatty() is False
    sys.stdout.flush()


def test_tee_is_installed_once_and_removed_on_shutdown(log_dir, cfg, monkeypatch):
    out, err = install_consoles(monkeypatch)
    errlog.setup(log_dir, tee_console=True)
    first = sys.stdout
    assert first is not out
    errlog.setup(log_dir, tee_console=True)                    # idempotent: not wrapped twice
    assert sys.stdout is first
    errlog.shutdown()
    assert sys.stdout is out and sys.stderr is err


def test_tee_off_leaves_the_console_alone(log_dir, cfg, monkeypatch):
    out, err = install_consoles(monkeypatch)
    errlog.setup(log_dir, tee_console=False)
    assert sys.stdout is out and sys.stderr is err


def test_warnings_bypass_the_tee(tmp_path, cfg, monkeypatch):
    out, err = install_consoles(monkeypatch)
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    errlog.setup(blocker / "logs", tee_console=True)
    errlog._enqueue("failures", {"kind": "x"})
    errlog.flush()
    assert err.getvalue().count("log writing failed") == 1       # reached the real console, once


def test_uncaught_exception_hook_records_and_chains(log_dir, cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a[0]))
    errlog.setup(log_dir, tee_console=False)
    assert sys.excepthook is not seen.append
    try:
        raise RuntimeError("nobody caught me")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())                         # Ctrl+C is not a failure
    errlog.flush()
    assert seen == [RuntimeError, KeyboardInterrupt]            # the previous hook still ran
    (rec,) = read_lines(log_dir / "failures.jsonl")
    assert rec["kind"] == "uncaught" and rec["component"] == "app"
    assert rec["error_type"] == "RuntimeError" and "nobody caught me" in rec["message"]
    assert "RuntimeError: nobody caught me" in rec["traceback"]
    errlog.shutdown()
    assert sys.excepthook is not errlog._excepthook


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_thread_exception_hook_records_the_thread_name(log_dir, cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.exc_type))
    errlog.setup(log_dir, tee_console=False)

    def worker():
        1 / 0

    t = threading.Thread(target=worker, name="boom-thread")
    t.start()
    t.join()
    errlog.flush()
    (rec,) = read_lines(log_dir / "failures.jsonl")
    assert rec["kind"] == "uncaught" and rec["component"] == "thread"
    assert rec["name"] == "boom-thread" and rec["error_type"] == "ZeroDivisionError"
    assert seen == [ZeroDivisionError]                          # the previous hook still ran
