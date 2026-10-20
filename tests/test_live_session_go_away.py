"""The server's GoAway and tool results that outlive their connection.

Seen in the real app on 2026-10-19: the Live connection is capped at ~10 min
(591.8 s, 23:02:51 → 23:12:43). The server sends `go_away` with `time_left`
first; main.py ignored it, so the server dropped the socket with 1008 "client
failed to close the connection after receiving a GoAway signal" — 56 times that
day. A `web_search` started 14 s before the drop finished into a dead session and
the user never heard the answer.
"""
from datetime import timedelta

from core import live_session as ls


# ── parse_time_left ──────────────────────────────────────────────────────────

def test_time_left_protobuf_duration_string():
    # The SDK hands the Duration over as its JSON form.
    assert ls.parse_time_left("50s") == 50.0
    assert ls.parse_time_left("1.500s") == 1.5
    assert ls.parse_time_left(" 7 ") == 7.0


def test_time_left_other_shapes():
    assert ls.parse_time_left(timedelta(seconds=12)) == 12.0
    assert ls.parse_time_left(30) == 30.0
    assert ls.parse_time_left(2.5) == 2.5


def test_time_left_missing_or_garbage_uses_default():
    assert ls.parse_time_left(None) == ls.GO_AWAY_DEFAULT_LEFT_S
    assert ls.parse_time_left("soon") == ls.GO_AWAY_DEFAULT_LEFT_S
    assert ls.parse_time_left("") == ls.GO_AWAY_DEFAULT_LEFT_S


def test_time_left_never_negative():
    assert ls.parse_time_left(-3) == 0.0
    assert ls.parse_time_left(timedelta(seconds=-1)) == 0.0


# ── go_away_should_reconnect ─────────────────────────────────────────────────

def _decide(left, playing=False, model_quiet=5.0, user_quiet=5.0):
    return ls.go_away_should_reconnect(
        left, playing=playing, model_quiet_s=model_quiet, user_quiet_s=user_quiet)


def test_quiet_moment_reconnects_at_once():
    assert _decide(50.0) is True


def test_waits_while_the_model_is_heard():
    assert _decide(50.0, playing=True) is False
    # Audio arrived a moment ago: more is probably on its way.
    assert _decide(50.0, model_quiet=0.2) is False


def test_waits_while_the_user_is_talking():
    assert _decide(50.0, user_quiet=0.3) is False


def test_deadline_wins_over_everything():
    # Past the margin the server's 1008 is coming: close it ourselves.
    m = ls.GO_AWAY_MARGIN_S
    assert _decide(m, playing=True, model_quiet=0.0, user_quiet=0.0) is True
    assert _decide(0.0, playing=True) is True
    assert _decide(m + 0.5, playing=True) is False


# ── result_entry / ResultMailbox ─────────────────────────────────────────────

def test_silent_results_are_not_kept():
    assert ls.result_entry("save_memory", {}, {"result": "ok", "silent": True}) is None
    e = ls.result_entry("web_search", {"query": "q"}, {"result": "answer"})
    assert e == {"name": "web_search", "args": {"query": "q"}, "result": "answer"}


def test_silent_scheduling_is_not_kept():
    # NON_BLOCKING tool scheduled SILENT: it announced itself already. The SDK
    # may hand the enum ("FunctionResponseScheduling.SILENT") or the string.
    assert ls.result_entry("call", {}, {"result": "ok"}, "SILENT") is None
    assert ls.result_entry("call", {}, {"result": "ok"}, "FunctionResponseScheduling.SILENT") is None
    assert ls.result_entry("call", {}, {"result": "ok"}, "WHEN_IDLE") is not None


def test_parked_result_is_taken_once():
    box = ls.ResultMailbox()
    e = ls.result_entry("web_search", {"query": "q"}, {"result": "answer"})
    box.park([e, None])
    assert box.pending
    assert box.take() == [e]
    assert not box.pending and box.take() == []


def test_answered_result_is_not_replayed():
    box = ls.ResultMailbox()
    box.sent([ls.result_entry("web_search", {}, {"result": "a"})])
    box.heard()             # model audio after the response…
    box.answered()          # …then turn_complete: the answer was given
    box.session_ended()
    assert box.take() == []


def test_turn_complete_without_audio_is_not_an_answer():
    # A tool turn passes through several turn_completes; one that brought no
    # audio after the response does not mean the user heard anything.
    box = ls.ResultMailbox()
    e = ls.result_entry("web_search", {}, {"result": "a"})
    box.heard()             # audio from BEFORE the response does not count
    box.sent([e])
    box.answered()
    box.session_ended()
    assert box.take() == [e]


def test_unanswered_result_survives_the_session():
    # Response sent, the answer was still being spoken when the connection went.
    box = ls.ResultMailbox()
    e = ls.result_entry("web_search", {}, {"result": "a"})
    box.sent([e])
    box.session_ended()
    assert box.take() == [e]


def test_put_back_keeps_order_ahead_of_newer():
    box = ls.ResultMailbox()
    a = ls.result_entry("t", {}, {"result": "a"})
    b = ls.result_entry("t", {}, {"result": "b"})
    box.park([b])
    box.put_back([a])
    assert box.take() == [a, b]


# ── orphan_prompt ────────────────────────────────────────────────────────────

def test_prompt_carries_tool_args_and_result():
    txt = ls.orphan_prompt([ls.result_entry(
        "web_search", {"query": "feminicídio"}, {"result": "Dados de 2024"})])
    assert "web_search" in txt and "feminicídio" in txt and "Dados de 2024" in txt
    # External content stays data (constitution, principle V).
    assert "not instructions" in txt


def test_prompt_cuts_long_results():
    txt = ls.orphan_prompt([ls.result_entry("web_search", {}, {"result": "x" * 50_000})])
    assert len(txt) < ls.ORPHAN_RESULT_LIMIT + 1_000
    assert "[truncated]" in txt
