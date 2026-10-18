"""T038a / FR-019b — the automatic report: when the user says "não funcionou" (and its
variants) the code itself writes the report, because in two real voice rounds the model
noted only 2 of 4 (it did the thing instead of writing it down).

Fixtures are the phrases of the second voice round; no SDK, no network."""
import json
import time
from types import SimpleNamespace as NS

import pytest

from core import errlog, llm_usage

SECRET = "unit-test-secret-value-0001"


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"k": SECRET}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    return log_dir


def speech(user=None, assistant=None, tc=False):
    return NS(usage_metadata=None, data=None,
              server_content=NS(turn_complete=tc,
                                input_transcription=NS(text=user) if user is not None else None,
                                output_transcription=NS(text=assistant) if assistant is not None else None))


def say(user, assistant=None):
    """One whole turn: the user's words, optionally the assistant's, then turn_complete."""
    llm_usage.live_message(speech(user=user, assistant=assistant))
    llm_usage.live_message(speech(tc=True))


def reports(log_dir):
    errlog.flush()
    path = log_dir / "reports.jsonl"
    return read_lines(path) if path.exists() else []


def tool_report(expected="x"):
    from actions import report_problem
    return report_problem.report_problem({"expected": expected})


# ── the trigger (pure) ───────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Não funcionou.",
    "Isso não deu certo, eu queria ver a previsão do tempo",
    "Deu errado aqui, ia abrir seu YouTube",
    "não funcionou, eu queria que ele abrisse o Excel com a minha planilha",
    "nao funcionou", "NAO FUNCIONOU", "NÃO DEU CERTO", "nao deu certo", "Deu Errado", "deu errado!",
    "não   funcionou", "olha, não\nfuncionou",
    "It didn't work", "that did not work", "it didn’t work at all", "DIDN'T WORK",
    "ontem eu pedi o Excel e não funcionou de novo",
])
def test_phrases_that_trigger(text):
    assert errlog.problem_trigger(text) is True


@pytest.mark.parametrize("text", [
    "Funcionou, obrigado", "isso funcionou muito bem", "deu certo", "funcionou", "Perfeito, deu certo!",
    "it worked", "that works fine", "it did work", "does it work",
    "o que não funciona nesse programa?", "não funciona", "desfuncionou", "naofuncionou", "vai dar errado",
    "", "   ", "abra o Excel",
])
def test_phrases_that_do_not(text):
    assert errlog.problem_trigger(text) is False


def test_trigger_is_whole_word_and_never_raises():
    assert errlog.problem_trigger("xdeu errado") is False
    assert errlog.problem_trigger(None) is False
    assert errlog.problem_trigger(12345) is False


# ── on turn_complete ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("phrase", [
    "Não funcionou.",
    "Isso não deu certo, eu queria ver a previsão do tempo",
    "Deu errado aqui, ia abrir seu YouTube",
    "não funcionou, eu queria que ele abrisse o Excel com a minha planilha",
])
def test_real_phrases_produce_an_automatic_report(on, phrase):
    seen = []
    errlog.bind(notify=seen.append, transcript=lambda: ["User: an earlier line", "Jarvis: an earlier answer"])
    errlog.note_tool("open_app", {"app": "excel"}, "Opened Excel")
    say(phrase, assistant="Vou resolver isso agora.")
    (rec,) = reports(on)
    assert rec["kind"] == "report" and rec["source"] == "auto"
    assert rec["expected"] == phrase and rec["happened"] is None
    assert rec["transcript"] == ["User: an earlier line", "Jarvis: an earlier answer",
                                 f"User (now): {phrase}", "Assistant (now): Vou resolver isso agora."]
    assert [t["name"] for t in rec["recent_tools"]] == ["open_app"]
    assert rec["recent_failures"] == []
    assert seen == ["SYS: Problem noted for triage."]


def test_a_sentence_broken_into_transcription_chunks(on):
    for chunk in ("Isso não", " deu", " certo, eu queria", " ver a previsão"):
        llm_usage.live_message(speech(user=chunk))
    assert reports(on) == []                                      # nothing until the turn is complete
    llm_usage.live_message(speech(tc=True))
    (rec,) = reports(on)
    assert rec["expected"] == "Isso não deu certo, eu queria ver a previsão"


def test_the_phrase_can_arrive_in_the_same_message_as_turn_complete(on):
    llm_usage.live_message(speech(user="Deu errado aqui", tc=True))
    (rec,) = reports(on)
    assert rec["source"] == "auto" and rec["expected"] == "Deu errado aqui"


@pytest.mark.parametrize("text", ["Funcionou, obrigado", "isso funcionou muito bem", "deu certo", "abra o Excel"])
def test_normal_turns_write_nothing(on, text):
    seen = []
    errlog.bind(notify=seen.append)
    say(text, assistant="Claro!")
    assert reports(on) == [] and seen == []


def test_only_the_users_words_trigger(on):
    say("conte uma piada", assistant="Esta piada não funcionou, pois deu errado de novo")
    assert reports(on) == []


def test_a_turn_complete_with_no_speech_triggers_nothing(on):
    llm_usage.live_message(speech(tc=True))
    llm_usage.live_message(speech(tc=True))
    assert reports(on) == []


def test_the_buffers_are_emptied_so_the_next_turn_does_not_repeat_it(on):
    say("não funcionou")
    say("obrigado")
    assert len(reports(on)) == 1


def test_a_secret_in_the_phrase_is_masked(on):
    say(f"não funcionou, minha chave é {SECRET} e eu queria o Excel")
    errlog.flush()
    raw = (on / "reports.jsonl").read_text(encoding="utf-8")
    assert SECRET not in raw and "‹redacted" in raw and "eu queria o Excel" in raw


def test_a_long_phrase_is_cut_like_any_other_text(on):
    say("não funcionou " + "x" * 900)
    (rec,) = reports(on)
    assert rec["expected"].endswith("chars]") and len(rec["expected"]) < 520


# ── no duplicates ────────────────────────────────────────────────────────────
def test_the_tool_in_the_same_turn_prevents_the_automatic_one(on):
    llm_usage.live_message(speech(user="Não funcionou, eu queria o Excel"))
    assert tool_report("abrir o Excel").startswith("Logged.")     # the model called it mid-turn
    llm_usage.live_message(speech(tc=True))
    (rec,) = reports(on)
    assert rec["source"] == "tool" and rec["expected"] == "abrir o Excel"


def test_the_tool_in_the_next_turn_is_a_separate_report(on):
    say("Não funcionou.", assistant="O que você queria?")         # automatic one, turn 1
    llm_usage.live_message(speech(user="Queria que tocasse música"))
    tool_report("tocar música")                                   # the model asked, then completed the expectation
    llm_usage.live_message(speech(tc=True))
    auto, tool = reports(on)
    assert (auto["source"], tool["source"]) == ("auto", "tool")
    assert tool["expected"] == "tocar música"


def test_the_tool_flag_only_covers_its_own_turn(on):
    tool_report("a")                                              # an explicit "anota esse problema"
    llm_usage.live_message(speech(user="anota isso"))
    llm_usage.live_message(speech(tc=True))
    say("Deu errado de novo")                                     # next turn: automatic one is NOT suppressed
    sources = [r["source"] for r in reports(on)]
    assert sources == ["tool", "auto"]


def test_tool_reports_always_say_where_they_came_from(on):
    tool_report("anota esse problema: a câmera não abriu")
    errlog.record_report("direct call")
    assert [r["source"] for r in reports(on)] == ["tool", "tool"]


def test_a_failed_tool_report_does_not_block_the_automatic_one(on, monkeypatch):
    monkeypatch.setattr(errlog, "_MAX_QUEUE", 0)                  # the queue refuses everything
    assert tool_report("x").startswith("Could not log it")
    monkeypatch.setattr(errlog, "_MAX_QUEUE", 10_000)
    say("Não funcionou")
    assert [r["source"] for r in reports(on)] == ["auto"]


# ── the screen line ──────────────────────────────────────────────────────────
def test_the_screen_line_appears_once_in_a_burst(on):
    seen = []
    errlog.bind(notify=seen.append)
    say("não funcionou")
    say("deu errado outra vez")
    say("não deu certo de novo")
    assert len(reports(on)) == 3                                  # every report is written…
    assert seen == ["SYS: Problem noted for triage."]            # …the line is shown once per 20 s


def test_no_screen_line_when_the_report_could_not_be_queued(log_dir, monkeypatch):
    seen = []
    errlog.bind(notify=seen.append)                               # before setup(): nothing can be written
    say("não funcionou")
    assert seen == []


def test_a_notify_that_raises_does_not_break_the_audio_path(on):
    def bad(_):
        raise RuntimeError("ui gone")
    errlog.bind(notify=bad)
    say("não funcionou")
    assert len(reports(on)) == 1


# ── what must NOT trigger it ─────────────────────────────────────────────────
def test_a_new_voice_session_forgets_a_half_heard_phrase(on):
    llm_usage.live_message(speech(user="não funcionou"))
    llm_usage.live_session_start("m", "Puck", False)
    llm_usage.live_message(speech(tc=True))
    assert reports(on) == []


def test_shutdown_does_not_write_a_report(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    llm_usage.live_message(speech(user="não funcionou"))
    errlog.shutdown()
    assert not (log_dir / "reports.jsonl").exists()


def test_automatic_failures_still_never_carry_a_transcript(on):
    errlog.bind(transcript=lambda: ["User: private"])
    errlog.record_failure("action", "demo", error_type="X", origin="actions/demo.py:run")
    say("não funcionou")
    errlog.flush()
    for rec in read_lines(on / "failures.jsonl"):
        assert "transcript" not in rec
    assert reports(on)[0]["transcript"][-1] == "User (now): não funcionou"


# ── cost on the audio thread ─────────────────────────────────────────────────
def test_turn_complete_with_the_trigger_stays_cheap(on):
    llm_usage.live_session_start("m", "Puck", False)
    llm_usage.live_message(speech(user="warmup"))
    samples = []
    for i in range(1500):
        llm_usage.live_message(speech(user="Isso não deu certo, eu queria ver a previsão do tempo"))
        t0 = time.perf_counter()
        llm_usage.live_message(speech(tc=True))
        samples.append((time.perf_counter() - t0) * 1000)
        if i % 100 == 99:
            errlog.flush()
    samples.sort()
    assert samples[int(len(samples) * 0.95)] < 0.5, samples[-5:]
