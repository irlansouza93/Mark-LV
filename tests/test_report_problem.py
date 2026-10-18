"""US3 — the `report_problem` tool and errlog.record_report (spec FR-017..FR-019a, research R13)."""
import ast
import json
import re
import shutil
import time
from pathlib import Path

import pytest

from core import action_loader, errlog

ROOT = errlog.ROOT
SECRET = "unit-test-secret-value-0001"
T0 = 1_700_000_000.0


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": SECRET}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    return log_dir


def handler():
    from actions import report_problem
    return report_problem.report_problem


def failure(ts, name, error_type="ValueError", component="plugin"):
    errlog._enqueue("failures", {"kind": "exception", "component": component, "name": name,
                                 "error_type": error_type, "origin": f"plugins/{name}.py:run"}, ts=ts)


# ── the handler end to end ───────────────────────────────────────────────────
def test_report_gets_conversation_tools_and_recent_failures(on, monkeypatch):
    monkeypatch.setattr(errlog, "_clock", lambda: T0)
    lines = [f"User: line {i}" if i % 2 == 0 else f"Jarvis: reply {i}" for i in range(8)]
    lines[7] = f"Jarvis: here is my key {SECRET}"                 # a secret that slipped into the talk
    errlog.bind(transcript=lambda: list(lines))
    for i in range(10):
        errlog.note_tool(f"tool{i}", {"i": i}, "Done.")
    failure(T0 - 60, "recent_a")
    failure(T0 - 540, "recent_b", "KeyError")
    failure(T0 - 1200, "twenty_minutes_ago")                     # outside the 10-minute window

    out = handler()({"expected": "abrir o Excel", "happened": "abriu o Word"})
    errlog.flush()

    assert out == "Logged. Confirm in one short sentence."
    (rec,) = read_lines(on / "reports.jsonl")
    assert rec["kind"] == "report" and rec["v"] == 1
    assert rec["expected"] == "abrir o Excel" and rec["happened"] == "abriu o Word"
    assert rec["transcript"][:5] == lines[2:7]                    # the LAST six, in order
    assert len(rec["transcript"]) == 6
    assert SECRET not in json.dumps(rec) and "‹redacted" in rec["transcript"][5]
    assert [t["name"] for t in rec["recent_tools"]] == [f"tool{i}" for i in range(2, 10)]
    assert set(rec["recent_tools"][0]) == {"ts", "name", "args", "result", "soft_failure"}
    fails = rec["recent_failures"]
    assert {f["name"] for f in fails} == {"recent_a", "recent_b"}
    for f in fails:
        assert set(f) == {"ts", "signature", "component", "name", "error_type"}
        assert re.fullmatch(r"[0-9a-f]{12}", f["signature"]) and f["component"] == "plugin"
        assert datetime_ok(f["ts"])


def datetime_ok(text):
    from datetime import datetime
    return datetime.fromisoformat(text).tzinfo is not None


def test_failures_are_listed_oldest_first(on, monkeypatch):
    monkeypatch.setattr(errlog, "_clock", lambda: T0)
    failure(T0 - 30, "newer")
    failure(T0 - 500, "older")
    handler()({"expected": "x"})
    errlog.flush()
    (rec,) = read_lines(on / "reports.jsonl")
    assert [f["name"] for f in rec["recent_failures"]] == ["older", "newer"] or \
        [f["name"] for f in rec["recent_failures"]] == ["newer", "older"]
    times = [f["ts"] for f in rec["recent_failures"]]
    assert times == sorted(times)


def test_window_edge_and_future_failures(on, monkeypatch):
    monkeypatch.setattr(errlog, "_clock", lambda: T0)
    failure(T0 - 599, "inside")
    failure(T0 - 601, "outside")
    handler()({"expected": "x"})
    errlog.flush()
    (rec,) = read_lines(on / "reports.jsonl")
    assert [f["name"] for f in rec["recent_failures"]] == ["inside"]


def test_a_failure_still_in_the_queue_is_seen_by_the_report(on, monkeypatch):
    # The report is built on the writer thread, after everything enqueued before it.
    monkeypatch.setattr(errlog, "_clock", lambda: T0)
    failure(T0 - 1, "just_now")
    handler()({"expected": "x"})                                  # no flush in between
    errlog.flush()
    (rec,) = read_lines(on / "reports.jsonl")
    assert [f["name"] for f in rec["recent_failures"]] == ["just_now"]


def test_burst_suppressed_failures_do_not_flood_the_report(on, monkeypatch):
    monkeypatch.setattr(errlog, "_clock", lambda: T0)
    for i in range(60):
        failure(T0 - 5, "loop")                                   # same signature, 60 times
    handler()({"expected": "x"})
    errlog.flush()
    (rec,) = read_lines(on / "reports.jsonl")
    assert len(rec["recent_failures"]) <= 20


def test_missing_fields(on):
    out = handler()({})
    handler()({"expected": "   ", "happened": ""})
    handler()({"expected": None, "happened": None})
    errlog.flush()
    assert out == "Logged. Confirm in one short sentence."
    recs = read_lines(on / "reports.jsonl")
    assert [r["expected"] for r in recs] == ["(not said)"] * 3
    assert [r["happened"] for r in recs] == [None] * 3
    assert all(r["transcript"] == [] and r["recent_tools"] == [] and r["recent_failures"] == [] for r in recs)


def test_transcript_lines_are_cut_and_garbage_providers_are_survived(on):
    errlog.bind(transcript=lambda: ["User: " + "x" * 900, "ok"])
    handler()({"expected": "a"})
    errlog.bind(transcript=lambda: (_ for _ in ()).throw(RuntimeError("provider exploded")))
    handler()({"expected": "b"})
    errlog.bind(transcript=lambda: None)
    handler()({"expected": "c"})
    errlog.bind(transcript=lambda: [None, 5, "text"])
    handler()({"expected": "d"})
    errlog.flush()
    a, b, c, d = read_lines(on / "reports.jsonl")
    assert a["transcript"][0].endswith("…[+406 chars]") and len(a["transcript"][0]) <= 520
    assert a["transcript"][1] == "ok"
    assert b["transcript"] == [] and c["transcript"] == []
    assert d["transcript"] == ["None", "5", "text"]


def test_report_without_a_bound_transcript(on):
    handler()({"expected": "a"})
    errlog.flush()
    assert read_lines(on / "reports.jsonl")[0]["transcript"] == []


# ── FR-017: automatic failures never carry the conversation ──────────────────
def test_automatic_failures_never_carry_a_transcript(on):
    errlog.bind(transcript=lambda: ["User: private talk"])
    errlog.record_failure("action", "demo", error_type="X", message="boom", origin="actions/demo.py:run")
    errlog.record_failure("action", "demo2", error_type="X", context={"transcript": ["sneaky"]},
                          origin="actions/demo.py:run")
    errlog._enqueue("failures", {"kind": "soft", "transcript": ["User: sneaky"], "component": "tool",
                                 "name": "t", "error_type": "soft:x", "origin": "main.py:f"})
    try:
        1 / 0
    except ZeroDivisionError as e:
        errlog.record_failure("tool", "t", exc=e)
    handler()({"expected": "something"})
    errlog.flush()
    for rec in read_lines(on / "failures.jsonl"):
        assert "transcript" not in rec
    assert "private talk" not in (on / "failures.jsonl").read_text(encoding="utf-8")
    assert read_lines(on / "reports.jsonl")[0]["transcript"] == ["User: private talk"]


# ── the tool never becomes a soft failure of itself ──────────────────────────
def test_report_problem_errors_are_not_soft_failures(on):
    errlog.note_tool("report_problem", {"expected": "x"}, "Could not log it: the log queue is full")
    errlog.note_tool("other_tool", {}, "Could not log it: same text, other tool")
    errlog.flush()
    recs = read_lines(on / "failures.jsonl")
    assert [r["name"] for r in recs] == ["other_tool"]            # only the other tool counts
    assert [t["soft_failure"] for t in errlog._recent_tools()] == [False, True]


# ── record_report outcome ────────────────────────────────────────────────────
def test_record_report_returns_none_when_queued_and_a_reason_when_not(log_dir):
    assert "not" in errlog.record_report("x") and "running" in errlog.record_report("x")     # before setup()
    assert handler()({"expected": "x"}).startswith("Could not log it: ")


def test_record_report_reports_a_dead_writer(on):
    assert errlog.record_report("x") is None
    errlog._stop_writer_for_test()
    reason = errlog.record_report("x")
    assert isinstance(reason, str) and "writer" in reason
    assert handler()({"expected": "x"}) == f"Could not log it: {reason}"


def test_record_report_reports_a_full_queue(on, monkeypatch):
    monkeypatch.setattr(errlog, "_MAX_QUEUE", 0)
    reason = errlog.record_report("x")
    assert isinstance(reason, str) and "full" in reason


def test_record_report_never_raises(on):
    class Evil:
        def __str__(self):
            raise RuntimeError("no str")
    errlog.record_report(Evil(), Evil())
    errlog.record_report(None)
    errlog.flush()


# ── instant (contract: < 5 ms, nothing but a queue put) ──────────────────────
def test_handler_is_instant(on):
    errlog.bind(transcript=lambda: [f"User: {'word ' * 40}"] * 6)
    for i in range(8):
        errlog.note_tool(f"t{i}", {"a": "b" * 300}, "Done.")
    h = handler()
    samples = []
    for _ in range(500):
        t0 = time.perf_counter()
        h({"expected": "abrir o Excel com a planilha", "happened": "abriu outra coisa"})
        samples.append((time.perf_counter() - t0) * 1000)
        if len(samples) % 100 == 0:
            errlog.flush()
    samples.sort()
    assert samples[int(len(samples) * 0.95)] < 1.0, samples[-5:]    # ms; the contract allows 5


# ── the declaration ──────────────────────────────────────────────────────────
def declaration_text(tool):
    parts = [tool["name"], tool["description"]]
    for key, prop in tool["parameters"]["properties"].items():
        parts += [key, prop["description"]]
    return "".join(parts)


def test_tool_declaration_shape_and_size():
    from actions import report_problem as mod
    tool = mod.TOOL
    assert tool["name"] == "report_problem" and tool["handler"] is mod.report_problem
    assert tool["parameters"]["type"] == "OBJECT" and tool["parameters"]["required"] == ["expected"]
    assert set(tool["parameters"]["properties"]) == {"expected", "happened"}
    assert all(p["type"] == "STRING" for p in tool["parameters"]["properties"].values())
    assert "behavior" not in tool                                   # blocking and instant
    assert len(declaration_text(tool)) <= 300                       # FR-018


def test_the_description_tells_the_model_to_log_first_and_ask_when_unsure():
    # T039 round 1: the model fixed the problem instead of writing it down. The wording is behaviour.
    from actions import report_problem as mod
    d = mod.TOOL["description"]
    assert d.startswith("Call FIRST")
    for phrase in ("não funcionou", "não deu certo", "deu errado", "then try to fix it", "ask before calling"):
        assert phrase in d, phrase


def test_the_action_loader_accepts_the_tool(tmp_path):
    adir = tmp_path / "actions"
    adir.mkdir()
    shutil.copy(ROOT / "actions" / "report_problem.py", adir / "report_problem.py")
    reg = action_loader.discover_actions(adir, reserved_names=set(), logger=lambda _m: None)
    assert reg.has("report_problem")
    decl = [d for d in reg.get_tool_declarations() if d["name"] == "report_problem"]
    assert len(decl) == 1


def test_the_name_does_not_collide_with_an_inline_tool():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    block = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "TOOL_DECLARATIONS" for t in node.targets):
            block = ast.get_source_segment(src, node)
    assert block, "TOOL_DECLARATIONS not found in main.py"
    inline = set(re.findall(r"""["']name["']\s*:\s*["'](\w+)["']""", block))
    assert len(inline) > 5 and "report_problem" not in inline


def test_a_handler_failure_comes_back_as_text(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("queue exploded")
    monkeypatch.setattr(errlog, "record_report", boom)
    out = handler()({"expected": "x"})
    assert out == "Could not log it: queue exploded"


# ── the sentence that triggered the report is still being spoken (T039, round 1) ─────────
from types import SimpleNamespace as NS

from core import llm_usage


def speech(user=None, assistant=None, tc=False, usage=None):
    """A Live message as the receive loop sees it: transcription chunks ride in server_content."""
    return NS(usage_metadata=usage, data=None,
              server_content=NS(turn_complete=tc,
                                input_transcription=NS(text=user) if user is not None else None,
                                output_transcription=NS(text=assistant) if assistant is not None else None))


def last_report(log_dir):
    errlog.flush()
    return read_lines(log_dir / "reports.jsonl")[-1]


def test_report_contains_the_sentence_being_spoken_right_now(on):
    errlog.bind(transcript=lambda: ["Jarvis: an old line"])
    for chunk in ("Não", " funcionou.", " Eu queria", " abrir a câmera"):       # arrives in pieces
        llm_usage.live_message(speech(user=chunk))
    handler()({"expected": "abrir a câmera"})
    rec = last_report(on)
    assert rec["transcript"] == ["Jarvis: an old line", "User (now): Não funcionou. Eu queria abrir a câmera"]


def test_assistant_draft_follows_the_user_line(on):
    llm_usage.live_message(speech(user="A câmera não abriu."))
    llm_usage.live_message(speech(assistant="Vou "))
    llm_usage.live_message(speech(assistant="verificar isso"))
    handler()({"expected": "câmera"})
    assert last_report(on)["transcript"] == ["User (now): A câmera não abriu.", "Assistant (now): Vou verificar isso"]


def test_only_the_last_six_lines_survive_and_now_lines_are_last(on):
    errlog.bind(transcript=lambda: [f"line {i}" for i in range(8)])
    llm_usage.live_message(speech(user="current user"))
    llm_usage.live_message(speech(assistant="current draft"))
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == ["line 4", "line 5", "line 6", "line 7",
                                              "User (now): current user", "Assistant (now): current draft"]


def test_buffers_are_emptied_by_turn_complete(on):
    errlog.bind(transcript=lambda: ["User: half a sentence"])      # main.py logged the turn at turn_complete
    llm_usage.live_message(speech(user="half a sentence", assistant="half an answer"))
    llm_usage.live_message(speech(tc=True))
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == ["User: half a sentence"]
    llm_usage.live_message(speech(user="new turn"))
    handler()({"expected": "y"})
    assert last_report(on)["transcript"][-1] == "User (now): new turn"


def test_a_message_that_both_speaks_and_completes_leaves_the_buffers_empty(on):
    errlog.bind(transcript=lambda: ["User: last words"])           # main.py logged the turn
    llm_usage.live_message(speech(user="last words", tc=True))
    assert errlog._turn_user == [] and errlog._turn_assistant == []
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == ["User: last words"]      # no "(now)" duplicate of what the log has


def test_without_a_session_log_the_request_words_stay_until_the_user_speaks_again(on):
    llm_usage.live_message(speech(user="last words", tc=True))
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == ["User (now): last words"]
    llm_usage.live_message(speech(user="another request"))
    llm_usage.live_message(speech(tc=True))
    handler()({"expected": "y"})
    assert last_report(on)["transcript"] == ["User (now): another request"]


def test_a_secret_in_the_current_speech_is_masked(on):
    llm_usage.live_message(speech(user=f"my key is {SECRET} please note it"))
    handler()({"expected": "x"})
    errlog.flush()
    raw = (on / "reports.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw and "‹redacted" in raw and "please note it" in raw


def test_current_speech_lines_are_cut_like_any_other(on):
    llm_usage.live_message(speech(user="w" * 900))
    handler()({"expected": "x"})
    line = last_report(on)["transcript"][-1]
    assert line.startswith("User (now): ") and line.endswith("…[+412 chars]")


def test_runaway_speech_is_bounded_in_memory(on):
    for _ in range(2000):
        llm_usage.live_message(speech(user="blah " * 20, assistant="bleh " * 20))      # 400 KB of chunks
    handler()({"expected": "x"})
    user_line, draft_line = last_report(on)["transcript"]
    assert user_line.startswith("User (now): ") and draft_line.startswith("Assistant (now): ")
    assert sum(len(c) for c in errlog._turn_user) < 8000 and sum(len(c) for c in errlog._turn_assistant) < 8000


def test_a_new_voice_session_forgets_the_old_turn(on):
    llm_usage.live_message(speech(user="stale words from the previous connection"))
    llm_usage.live_session_start("m", "Puck", False)
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == []


def test_the_audio_path_only_concatenates_strings(on, monkeypatch):
    calls = []
    real = errlog._enqueue
    monkeypatch.setattr(errlog, "_enqueue", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    for chunk in ("a", "b", "c"):
        llm_usage.live_message(speech(user=chunk, assistant=chunk))
    assert calls == []                                              # no queue, no I/O, no cost math
    assert errlog._turn_user == ["a", "b", "c"]


@pytest.mark.parametrize("bad", [None, "", "   ", 5])
def test_speech_garbage_is_ignored(on, bad):
    llm_usage.live_message(speech(user=bad, assistant=bad))
    llm_usage.live_message(NS(usage_metadata=None, server_content=NS(turn_complete=False)))        # no transcription attrs at all
    llm_usage.live_message(NS(usage_metadata=None, server_content=None))
    handler()({"expected": "x"})
    assert last_report(on)["transcript"] == []


def test_the_receive_loop_cost_stays_tiny_with_transcription(on):
    llm_usage.live_session_start("m", "Puck", False)
    msg = speech(user="palavra ", assistant="resposta ")
    samples = []
    for i in range(4000):
        t0 = time.perf_counter()
        llm_usage.live_message(msg)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    assert samples[int(len(samples) * 0.95)] < 0.2
