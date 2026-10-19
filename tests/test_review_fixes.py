"""Fixes from the independent review of spec 001 (T045). One section per finding."""
import asyncio
import io
import json
import os
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core import errlog, llm_usage
from core.redact import load_config_secrets, redact

ROOT = errlog.ROOT
# A 47-character secret, like a real key: long enough to straddle a cut with 35+ characters on one side.
KEY47 = "Zq7" + "kP2mXw9" * 6 + "b4"
assert len(KEY47) == 47


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def leaks(raw: str, secret: str, n: int = 8) -> bool:
    """Does any run of ``n`` characters of the secret appear in ``raw``?"""
    return any(secret[i:i + n] in raw for i in range(len(secret) - n + 1))


@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": KEY47}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    return log_dir


def all_log_text(log_dir):
    errlog.flush()
    return "".join(p.read_text(encoding="utf-8") for p in log_dir.glob("*.jsonl")) + \
        ("".join(p.read_text(encoding="utf-8") for p in log_dir.glob("jarvis.log*")))


# ═════ 1. cutting before filtering must not leave a piece of a secret ════════════════════════
@pytest.mark.parametrize("position", [480, 1000, 1010, 1100])        # around 500 and around the caller's cut
def test_a_secret_crossing_a_cut_leaves_no_recognisable_prefix(on, position):
    pad = "x" * position
    errlog.note_tool("some_tool", {"q": pad + KEY47 + "y" * 900},
                     "Could not do that: " + pad + KEY47 + "z" * 900)
    from actions import report_problem
    report_problem.report_problem({"expected": "x"})
    raw = all_log_text(on)
    assert not leaks(raw, KEY47), "a piece of the key reached the disk"


def test_the_ring_keeps_enough_slack_for_the_final_cut(on):
    errlog.note_tool("t", {"q": "a" * 5000}, "r" * 5000)
    entry = errlog._recent_tools()[-1]
    assert 500 < len(entry["args"]["q"]) < 1100 and entry["args"]["q"].endswith("chars]")


def test_final_truncation_still_counts_the_real_number_of_removed_characters(on):
    errlog._enqueue("failures", {"message": "m" * 5000, "traceback": "t" * 100_000,
                                 "kind": "soft", "component": "tool", "name": "n",
                                 "error_type": "soft:x", "origin": "main.py:f"})
    errlog.flush()
    (rec,) = read_lines(on / "failures.jsonl")
    assert rec["message"] == "m" * 500 + "…[+4500 chars]"
    assert rec["traceback"] == "t" * 8000 + "…[+92000 chars]"


def test_a_secret_split_by_the_console_tee_is_still_masked(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": KEY47}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    errlog.setup(log_dir, tee_console=True)
    # One huge line with no newline whose key sits exactly where the tee splits runaway lines.
    sys.stdout.write("w " * 3990 + KEY47 + " tail words\n")
    sys.stdout.write("v " * 4010 + KEY47 + " more\n")
    errlog.flush()
    raw = (log_dir / "jarvis.log").read_text(encoding="utf-8")
    assert raw.count(" OUT ") >= 2                              # the runaway line really was split
    assert not leaks(raw, KEY47)


def test_the_writer_bounds_what_the_filter_has_to_scan(on):
    big = "x" * 2_000_000
    t0 = time.perf_counter()
    errlog._write_text("OUT", big + KEY47, time.time())
    text_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    errlog._write_record("failures", {"message": big, "kind": "soft", "component": "t", "name": "n",
                                      "error_type": "e", "origin": "a.py:f", "context": {"result": big}},
                         time.time(), None)
    rec_ms = (time.perf_counter() - t0) * 1000
    assert text_ms < 30 and rec_ms < 60, (text_ms, rec_ms)


def test_a_neighbouring_thread_is_not_stalled_by_a_huge_line(on):
    gaps = []
    stop = threading.Event()

    def neighbour():
        last = time.perf_counter()
        while not stop.is_set():
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
            time.sleep(0.0002)
    t = threading.Thread(target=neighbour)
    t.start()
    time.sleep(0.05)
    for _ in range(5):
        errlog._enqueue_text("OUT", "x" * 2_000_000)
    errlog.flush(10)
    stop.set()
    t.join()
    assert max(gaps) < 0.020, max(gaps)                        # was 33.5 ms for a 2 MB line


# ═════ 2. the real key format and the config read ═════════════════════════════════════════════
REAL_SHAPE = "AQ." + "Ab8RN6Kq" * 6 + "Zz"                     # 'AQ.' + dots, dashes: 53 chars like the real one
REAL_SHAPE2 = "AQ.Ab8-RN6_Kq.9xZ" + "-abcDEF123" * 3


def test_aq_keys_are_masked_by_shape():
    for key in (REAL_SHAPE, REAL_SHAPE2):
        out = redact(f"the key {key} leaked")
        assert key not in out and "‹redacted:AQ." in out


def test_keys_outside_dicts_are_masked_by_name():
    cases = [
        "x-goog-api-key: sekret-value-1234",
        "headers={'x-goog-api-key': 'sekret-value-1234'}",
        "{'api_key': 'sekret-value-1234', 'other': 1}",
        'GET /v1?api_key="sekret-value-1234"',
        "api-key=sekret-value-1234;",
        "token: sekret-value-1234",
        "the secret = sekret-value-1234",
        '{"password": "sekret-value-1234"}',
    ]
    for text in cases:
        out = redact(text)
        assert "sekret-value-1234" not in out, text


def test_prose_and_counters_are_not_swallowed():
    for text in ("the secret to a good sauce", "max_tokens: 256", "tokens used 5000", "keyboard: us",
                 "password reset email sent", "Opened Excel in 1.2 s"):
        assert redact(text) == text, text


def test_config_secrets_come_from_sensitive_names_and_plugin_config_only(tmp_path):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({
        "gemini_api_key": REAL_SHAPE,
        "hud_style": "model:some-long-avatar-model-name",
        "persona": "a long persona string, sixteen plus",
        "voice_name": "SomeVoiceNameThatIsLong",
        "plugin_config": {"telegram": {"chat_id_value_long": "c" * 20, "bot": {"nested": "n" * 20}}},
        "plugins_enabled": {"a_plugin_with_a_long_name_here": True},
        "something_token": "t" * 20,
        "short_key": "tiny",
    }), encoding="utf-8")
    assert set(load_config_secrets(cfg)) == {REAL_SHAPE, "c" * 20, "n" * 20, "t" * 20}


def test_a_hud_style_value_is_no_longer_masked(on, tmp_path, monkeypatch):
    cfg = tmp_path / "k2.json"
    cfg.write_text(json.dumps({"gemini_api_key": KEY47, "hud_style": "model:avatar-long-name-here"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog._enqueue("failures", {"message": "style is model:avatar-long-name-here", "kind": "soft",
                                 "component": "t", "name": "n", "error_type": "e", "origin": "a.py:f"})
    errlog.flush()
    (rec,) = read_lines(on / "failures.jsonl")
    assert rec["message"] == "style is model:avatar-long-name-here"


def test_a_failed_config_read_keeps_the_previous_secrets_and_retries(on, tmp_path):
    cfg = errlog._CONFIG_PATH

    def record(msg):
        errlog._enqueue("failures", {"message": msg, "kind": "soft", "component": "t", "name": "n",
                                     "error_type": "e", "origin": "a.py:f"})
        errlog.flush()
        return read_lines(on / "failures.jsonl")[-1]["message"]

    assert KEY47 not in record(f"first {KEY47}")
    stamp = time.time() + 10
    cfg.write_text("{ this is being rewritten", encoding="utf-8")        # mid-rewrite: not valid JSON
    os.utime(cfg, (stamp, stamp))
    assert KEY47 not in record(f"during the rewrite {KEY47}")           # the previous secrets still protect
    new_key = "N" + "e1w" * 15 + "KEYZ"
    cfg.write_text(json.dumps({"gemini_api_key": new_key}), encoding="utf-8")
    os.utime(cfg, (stamp, stamp))                                       # SAME mtime as the failed read
    assert new_key not in record(f"after {new_key}")                    # so the failed mtime was not remembered


def test_a_missing_config_file_keeps_the_previous_secrets(on):
    cfg = errlog._CONFIG_PATH
    errlog._enqueue("failures", {"message": "warm", "kind": "soft", "component": "t", "name": "n",
                                 "error_type": "e", "origin": "a.py:f"})
    errlog.flush()
    cfg.unlink()
    errlog._enqueue("failures", {"message": f"gone {KEY47}", "kind": "soft", "component": "t", "name": "n2",
                                 "error_type": "e", "origin": "a.py:f"})
    errlog.flush()
    assert KEY47 not in all_log_text(on)


# ═════ 3. several turn_completes for one request ══════════════════════════════════════════════
def speech(user=None, assistant=None, tc=False):
    return NS(usage_metadata=None, data=None,
              server_content=NS(turn_complete=tc,
                                input_transcription=NS(text=user) if user is not None else None,
                                output_transcription=NS(text=assistant) if assistant is not None else None))


def tool_report(expected="x"):
    from actions import report_problem
    return report_problem.report_problem({"expected": expected})


def reports(log_dir):
    errlog.flush()
    path = log_dir / "reports.jsonl"
    return read_lines(path) if path.exists() else []


def test_auto_then_tool_in_one_request_are_linked(on):
    llm_usage.live_message(speech(user="Não funcionou, eu queria o Excel"))
    errlog.note_tool("open_app", {"app": "excel"}, "Opened Excel")
    llm_usage.live_message(speech(tc=True))                                # turn_complete 1 (tool call turn)
    assert tool_report("abrir o Excel").startswith("Logged.")             # the model's continuation
    llm_usage.live_message(speech(assistant="Pronto.", tc=True))           # turn_complete 2
    auto, tool = reports(on)
    assert (auto["source"], tool["source"]) == ("auto", "tool")
    assert tool["follows_auto"] == auto["ts"]
    assert "follows_auto" not in auto
    assert tool["transcript"][-1].endswith("Não funcionou, eu queria o Excel")        # the user's sentence rides along


def test_the_users_sentence_is_not_duplicated_when_the_session_log_has_it(on):
    errlog.bind(transcript=lambda: ["User: Não funcionou, eu queria o Excel", "Jarvis: abri"])
    llm_usage.live_message(speech(user="Não funcionou, eu queria o Excel"))
    llm_usage.live_message(speech(tc=True))
    tool_report("abrir o Excel")
    auto, tool = reports(on)
    assert sum("queria o Excel" in line for line in tool["transcript"]) == 1


def test_tool_first_then_several_turn_completes_never_writes_an_automatic_one(on):
    llm_usage.live_message(speech(user="Não funcionou, eu queria o Excel"))
    tool_report("abrir o Excel")
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_message(speech(assistant="ok", tc=True))
    (rec,) = reports(on)
    assert rec["source"] == "tool" and "follows_auto" not in rec


def test_a_second_turn_complete_without_new_speech_does_not_repeat_the_auto_report(on):
    llm_usage.live_message(speech(user="não funcionou"))
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_message(speech(tc=True))
    assert len(reports(on)) == 1


def test_new_user_speech_starts_a_new_request(on):
    llm_usage.live_message(speech(user="não funcionou"))
    llm_usage.live_message(speech(tc=True))
    tool_report("a")                                                       # same request: linked
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_message(speech(user="deu errado de novo"))              # NEW request
    llm_usage.live_message(speech(tc=True))
    tool_report("b")                                                       # tool after the second auto
    llm_usage.live_message(speech(user="obrigado"))
    llm_usage.live_message(speech(tc=True))
    tool_report("c")                                                       # an unrelated later report
    got = reports(on)
    assert [r["source"] for r in got] == ["auto", "tool", "auto", "tool", "tool"]
    assert got[1]["follows_auto"] == got[0]["ts"] and got[3]["follows_auto"] == got[2]["ts"]
    assert "follows_auto" not in got[4]


def test_a_new_voice_session_resets_the_request(on):
    llm_usage.live_message(speech(user="não funcionou"))
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_session_start("m", "Puck", False)
    tool_report("fresh")
    assert "follows_auto" not in reports(on)[-1]


# ═════ 4. rotation that cannot rename (Windows: file open elsewhere) ═════════════════════════
def test_a_failed_rotation_keeps_appending_and_loses_nothing(on, monkeypatch, capsys):
    monkeypatch.setitem(errlog._LIMITS, "failures", (1500, 2))
    monkeypatch.setattr(errlog, "_ROTATE_RETRY_RECORDS", 10_000)
    seen = []
    errlog.bind(notify=seen.append)
    real_rename = os.rename

    def locked(src, dst):
        raise PermissionError(32, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(os, "rename", locked)
    for i in range(40):
        errlog._enqueue("failures", {"kind": "soft", "message": f"rec {i} " + "x" * 60, "component": "t",
                                     "name": f"n{i}", "error_type": "e", "origin": "a.py:f"})
    errlog.flush()
    recs = read_lines(on / "failures.jsonl")
    assert [r["message"].split()[1] for r in recs] == [str(i) for i in range(40)]      # all 40, in order
    assert (on / "failures.jsonl").stat().st_size > 1500                                # it went past the limit
    err = capsys.readouterr().err
    assert err.count("could not rotate") == 1 and len(seen) == 1                                # one warning, not 40
    monkeypatch.setattr(os, "rename", real_rename)


def test_rotation_is_retried_after_a_while(on, monkeypatch):
    monkeypatch.setitem(errlog._LIMITS, "failures", (1500, 2))
    monkeypatch.setattr(errlog, "_ROTATE_RETRY_RECORDS", 5)
    real_rename = os.rename
    state = {"locked": True}

    def sometimes(src, dst):
        if state["locked"]:
            raise PermissionError(32, "in use")
        return real_rename(src, dst)
    monkeypatch.setattr(os, "rename", sometimes)

    def burst(n, start):
        for i in range(n):
            errlog._enqueue("failures", {"kind": "soft", "message": f"rec {start + i} " + "x" * 60, "component": "t",
                                         "name": f"n{start + i}", "error_type": "e", "origin": "a.py:f"})
        errlog.flush()
    burst(30, 0)
    assert not (on / "failures.jsonl.1").exists()
    state["locked"] = False                                     # the other process let go
    burst(12, 100)
    assert (on / "failures.jsonl.1").exists()                   # rotation finally happened
    total = sum(len(read_lines(p)) for p in on.glob("failures.jsonl*"))
    assert total == 42                                          # nothing lost across the failed and the good rotation


# ═════ 6. the phase and the model of a live failure (main.py is not importable: read it) ═════
def _live_failure_hook_source():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    i = src.index('errlog.record_failure("live", "live_connect"')
    return src[i - 400:i + 900]


def test_main_marks_a_drop_after_connecting_as_receive():
    block = _live_failure_hook_source()
    assert 'phase="receive" if _connected else' in block.replace("\n", " ") or \
        '"receive" if _connected else' in block.replace("\n", " ")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert re.search(r'print\("\[JARVIS\] Connected\."\)\s*\n\s*_connected = True', src)


def test_main_hook_cannot_hit_an_unbound_local():
    block = _live_failure_hook_source()
    assert "live_model" not in block and '"model": LIVE_MODEL' in block
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    first = src.index("_end_reason, _end_ts, _connected, _resumed_with =")
    assert first < src.index("_gemini.live_model()")           # initialised before anything that can raise


# ═════ 7. uncaught exceptions tell the user on screen ═════════════════════════════════════════
MESSAGE = "SYS: Internal error — logged for triage."


def test_excepthooks_notify_once_a_minute(on, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)
    monkeypatch.setattr(errlog, "_prev_excepthook", lambda *a: None)
    monkeypatch.setattr(errlog, "_prev_thread_hook", lambda a: None)
    try:
        1 / 0
    except ZeroDivisionError:
        sys.excepthook(*sys.exc_info())
        sys.excepthook(*sys.exc_info())
    errlog._thread_excepthook(NS(exc_type=KeyError, exc_value=KeyError("k"), exc_traceback=None,
                                 thread=NS(name="worker")))
    loop = NS(default_exception_handler=lambda c: None)
    errlog.asyncio_exception_handler(loop, {"message": "boom", "exception": RuntimeError("x")})
    assert seen == [MESSAGE]
    errlog.notify_once("uncaught", "again", every_s=0)         # the key is "uncaught", rate-limited to 60 s
    assert seen[-1] == "again"


def test_ctrl_c_and_system_exit_do_not_notify(on, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)
    monkeypatch.setattr(errlog, "_prev_excepthook", lambda *a: None)
    monkeypatch.setattr(errlog, "_prev_thread_hook", lambda a: None)
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())
    errlog._thread_excepthook(NS(exc_type=SystemExit, exc_value=SystemExit(0), exc_traceback=None, thread=NS(name="t")))
    assert seen == []


def test_no_screen_line_when_the_failure_could_not_be_queued(log_dir, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)                            # before setup(): nothing is recorded
    errlog.asyncio_exception_handler(NS(default_exception_handler=lambda c: None),
                                     {"message": "m", "exception": RuntimeError("x")})
    assert seen == []


# ═════ suggestions ═══════════════════════════════════════════════════════════════════════════
def test_session_end_can_carry_the_time_it_really_ended(on, monkeypatch):
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    llm_usage.live_session_start("m", "Puck", False)
    t_end = time.time() + 30                                   # the error happened 30 s "later"; the finally ran after a backoff
    llm_usage.live_session_end("error", t_end)
    errlog.flush()
    lines = [json.loads(l) for f in sorted(on.glob("usage-2*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]
    end = [l for l in lines if l.get("event") == "end"][0]
    assert 29 < end["duration_s"] < 31 and end["end_reason"] == "error"


def test_session_end_without_a_time_uses_now(on, monkeypatch):
    monkeypatch.setattr(llm_usage, "get_llm_billing", lambda: "free")
    llm_usage.live_session_start("m", "Puck", False)
    llm_usage.live_session_end("shutdown")
    errlog.flush()
    lines = [json.loads(l) for f in sorted(on.glob("usage-2*.jsonl")) for l in f.read_text(encoding="utf-8").splitlines()]
    assert [l for l in lines if l.get("event") == "end"][0]["duration_s"] < 5


def test_main_clears_the_live_session_id_after_the_session():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    i = src.index("llm_usage.live_session_end(")
    assert "errlog.set_live_session(None)" in src[i:i + 200]


def test_exactly_twelve_months_of_usage_are_kept():
    names = [f"usage-{y}-{m:02d}.jsonl" for y, m in ((2025, 9), (2025, 10), (2025, 11), (2026, 10))]
    gone = errlog.months_to_delete(names, date(2026, 10, 16))
    assert sorted(gone) == ["usage-2025-09.jsonl", "usage-2025-10.jsonl"]    # kept: Nov 2025 .. Oct 2026 = 12 months


def test_ladder_message_when_quota_and_outage_are_both_present():
    reasons = {"a": "quota", "b": "quota", "c": "quota", "d": "unavailable", "e": "timeout", "f": "gone"}
    msg = errlog.ladder_message(reasons)
    assert re.fullmatch(r"SYS: Gemini free quota used up on 3 models and 3 not answering — quota back at \d\d:\d\d\.", msg)


def test_ladder_message_predominance():
    assert errlog.ladder_message({"a": "quota", "b": "quota", "c": "quota", "d": "timeout"}).startswith(
        "SYS: Gemini free quota used up on 3")
    assert errlog.ladder_message({"a": "quota", "b": "timeout", "c": "timeout"}).startswith(
        "SYS: Gemini models not answering")


def test_a_tee_without_a_stream_behaves_like_a_closed_console():
    tee = errlog._Tee(None, "OUT")
    assert tee.isatty() is False
    assert tee.encoding == "utf-8"
    with pytest.raises(OSError):
        tee.fileno()
    assert tee.write("hello\n") == 6
    tee.flush()


def test_a_tee_delegates_when_there_is_a_stream():
    class Console(io.StringIO):
        encoding = "cp1252"

        def isatty(self):
            return True
    tee = errlog._Tee(Console(), "OUT")
    assert tee.isatty() is True and tee.encoding == "cp1252"
