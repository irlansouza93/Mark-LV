"""core/wait_cue.py — the watchdog (Limits, WaitWatchdog) driven by a fake clock.

No microphone, no network, no real time: every instant is set by the test."""
import pytest

from core.wait_cue import Limits, WaitWatchdog

LIM = Limits(enabled=True, thinking_ms=1500, phrase_ms=4000, give_up_ms=20000)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def adv(self, seconds):
        self.t += seconds


@pytest.fixture
def clk():
    return Clock()


@pytest.fixture
def wd(clk):
    return WaitWatchdog(LIM, clock=clk)


def run(wd, clk, seconds, step=0.1):
    """Tick every 100 ms for ``seconds``; return (actions in order, records)."""
    actions, records = [], []
    for _ in range(round(seconds / step)):
        clk.adv(step)
        acts, rec = wd.tick()
        actions += acts
        if rec:
            records.append(rec)
    return actions, records


# ── Limits ───────────────────────────────────────────────────────────────────
def test_limits_defaults_and_frozen():
    lim = Limits()
    assert (lim.enabled, lim.thinking_ms, lim.phrase_ms, lim.give_up_ms) == (True, 1500, 4000, 20000)
    with pytest.raises(Exception):
        lim.thinking_ms = 1


def test_limits_from_config():
    lim = Limits.from_config({"enabled": False, "thinking_ms": 2000, "phrase_ms": 5000, "give_up_ms": 30000})
    assert lim == Limits(False, 2000, 5000, 30000)


def test_limits_from_config_missing_fields_use_defaults():
    assert Limits.from_config({}) == Limits()


# ── a quick answer ───────────────────────────────────────────────────────────
def test_audio_before_thinking_means_no_action_and_a_latency_record(wd, clk):
    wd.user_speech()
    acts, records = run(wd, clk, 1.0)
    assert acts == [] and records == []
    clk.adv(0.2)
    rec = wd.model_audio()
    assert rec["outcome"] == "audio" and rec["latency_ms"] == 1200
    assert (rec["source"], rec["tool"], rec["thinking"], rec["phrase"], rec["gave_up"]) == ("voice", False, False, None, False)
    assert rec["cancel_reason"] is None and rec["enabled"] is True
    assert rec["limits"] == {"thinking_ms": 1500, "phrase_ms": 4000, "give_up_ms": 20000}


def test_waiting_property_follows_the_episode(wd, clk):
    assert wd.waiting is False
    wd.user_speech()
    assert wd.waiting is True
    wd.model_audio()
    assert wd.waiting is False


# ── thinking and phrase ──────────────────────────────────────────────────────
def test_thinking_then_phrase_each_once_at_their_limits(wd, clk):
    wd.user_speech()
    clk.adv(1.4)
    assert wd.tick() == ([], None) and wd.thinking_shown is False
    clk.adv(0.1)
    assert wd.tick() == (["thinking"], None) and wd.thinking_shown is True
    clk.adv(2.4)
    assert wd.tick() == ([], None)
    clk.adv(0.1)
    assert wd.tick() == (["phrase"], None)
    clk.adv(5)
    assert wd.tick() == ([], None)                           # neither repeats


def test_a_late_first_tick_emits_both_in_order(wd, clk):
    wd.user_speech()
    clk.adv(4.5)
    assert wd.tick() == (["thinking", "phrase"], None)


def test_the_record_tells_what_the_net_did(wd, clk):
    wd.user_speech()
    clk.adv(4.1)
    wd.tick()
    wd.phrase_played("voice")
    clk.adv(1.0)
    rec = wd.model_audio()
    assert (rec["thinking"], rec["phrase"], rec["latency_ms"]) == (True, "voice", 5100)
    wd.turn_complete()
    wd.user_speech()
    clk.adv(4.1)
    wd.tick()
    wd.phrase_played("tone")
    assert wd.model_audio()["phrase"] == "tone"


def test_phrase_is_never_emitted_after_the_first_audio(wd, clk):
    wd.user_speech()
    clk.adv(3.9)
    wd.model_audio()
    clk.adv(10)
    assert wd.tick() == ([], None)


def test_speech_pushes_the_anchor(wd, clk):
    wd.user_speech()
    clk.adv(1.0)
    wd.user_speech()                                         # speech goes on
    clk.adv(1.0)
    assert wd.tick() == ([], None)                           # 1.0 s since the last chunk
    clk.adv(0.6)
    assert wd.tick() == (["thinking"], None)
    clk.adv(0.5)
    assert wd.model_audio()["latency_ms"] == 2100            # measured from the last chunk


# ── no reply / exchange ──────────────────────────────────────────────────────
def test_turn_complete_without_audio_is_no_reply_and_silences_the_net(wd, clk):
    wd.user_speech()
    clk.adv(0.5)
    rec = wd.turn_complete()
    assert rec["outcome"] == "no_reply" and rec["latency_ms"] is None
    clk.adv(30)
    assert wd.tick() == ([], None) and wd.waiting is False


def test_late_transcription_after_audio_is_ignored_until_turn_complete(wd, clk):
    wd.user_speech()
    clk.adv(0.5)
    assert wd.model_audio() is not None
    wd.user_speech()                                         # the final transcript arrives late
    assert wd.waiting is False
    clk.adv(30)
    assert wd.tick() == ([], None)
    assert wd.model_audio() is None                          # more audio of the same answer: nothing
    assert wd.turn_complete() is None                        # exchange ends, no record
    wd.user_speech()
    assert wd.waiting is True                                # a new episode again


def test_user_text_opens_an_episode_with_source_text(wd, clk):
    wd.user_text()
    assert wd.waiting is True
    clk.adv(2.0)
    assert wd.tick() == (["thinking"], None)
    assert wd.model_audio()["source"] == "text"


# ── IDLE: the app itself sent text to the model (e.g. proactive mode) ───────
def test_model_audio_and_turn_complete_in_idle_are_no_ops(wd, clk):
    assert wd.model_audio() is None
    assert wd.turn_complete() is None
    assert wd.waiting is False
    wd.user_speech()                                         # still opens a normal episode afterwards
    assert wd.waiting is True


def test_idle_does_not_enter_exchange(wd, clk):
    wd.model_audio()                                         # IDLE audio must not put us in EXCHANGE
    wd.user_speech()
    assert wd.waiting is True


def test_tick_in_idle_returns_nothing(wd, clk):
    assert wd.tick() == ([], None)
    clk.adv(100)
    assert wd.tick() == ([], None)


# ── tools and giving up ──────────────────────────────────────────────────────
def test_give_up_after_the_limit_without_tools(wd, clk):
    wd.user_speech()
    acts, records = run(wd, clk, 20.2)         # +0.2: float drift of 200 ticks
    assert acts == ["thinking", "phrase", "give_up"]
    (rec,) = records
    assert rec["outcome"] == "gave_up" and rec["gave_up"] is True and rec["latency_ms"] is None
    assert wd.waiting is False
    assert wd.tick() == ([], None)                           # closed: nothing more


def test_no_give_up_while_a_tool_runs(wd, clk):
    wd.user_speech()
    clk.adv(1.0)
    wd.tool_started()
    acts, records = run(wd, clk, 60.0)
    assert "give_up" not in acts and records == []
    assert wd.waiting is True


def test_give_up_counts_from_the_end_of_the_last_tool(wd, clk):
    wd.user_speech()
    wd.tool_started()
    clk.adv(30.0)
    wd.tool_finished()
    clk.adv(19.9)
    acts, rec = wd.tick()
    assert "give_up" not in acts and rec is None
    clk.adv(0.2)
    acts, rec = wd.tick()
    assert "give_up" in acts and rec["gave_up"] is True and rec["tool"] is True


def test_two_overlapping_tools_block_give_up_until_both_end(wd, clk):
    wd.user_speech()
    wd.tool_started()
    wd.tool_started()
    wd.tool_finished()
    clk.adv(40.0)
    assert "give_up" not in wd.tick()[0]
    wd.tool_finished()
    clk.adv(20.1)
    assert "give_up" in wd.tick()[0]


def test_extra_tool_finished_does_not_go_negative(wd, clk):
    wd.user_speech()
    wd.tool_finished()
    wd.tool_started()
    clk.adv(40.0)
    assert "give_up" not in wd.tick()[0]


def test_tool_flag_only_when_the_tool_came_before_the_first_audio(wd, clk):
    wd.user_speech()
    wd.tool_started()
    clk.adv(2.0)
    wd.tool_finished()
    assert wd.model_audio()["tool"] is True
    wd.turn_complete()
    wd.user_speech()
    clk.adv(1.0)
    rec = wd.model_audio()
    wd.tool_started()                                        # after the audio: not this episode
    assert rec["tool"] is False


def test_tool_events_outside_an_episode_do_not_open_one(wd, clk):
    wd.tool_started()
    wd.tool_finished()
    assert wd.waiting is False
    wd.user_speech()
    assert wd.model_audio()["tool"] is False


# ── cancel ───────────────────────────────────────────────────────────────────
def test_cancel_closes_with_the_reason(wd, clk):
    wd.user_speech()
    clk.adv(2.0)
    wd.tick()
    rec = wd.cancel("interrupt")
    assert rec["outcome"] == "cancelled" and rec["cancel_reason"] == "interrupt"
    assert rec["thinking"] is True and rec["latency_ms"] is None
    assert wd.waiting is False and wd.tick() == ([], None)


def test_cancel_without_an_episode_returns_none(wd, clk):
    assert wd.cancel("muted") is None


def test_cancel_in_exchange_ends_it_without_a_record(wd, clk):
    wd.user_speech()
    wd.model_audio()
    assert wd.cancel("interrupt") is None
    wd.user_speech()
    assert wd.waiting is True                                # back to IDLE: speech opens an episode


# ── disabled ─────────────────────────────────────────────────────────────────
def test_disabled_never_emits_actions_but_still_measures(clk):
    wd = WaitWatchdog(Limits(enabled=False), clock=clk)
    wd.user_speech()
    acts, records = run(wd, clk, 30.0)
    assert acts == [] and records == [] and wd.thinking_shown is False
    rec = wd.model_audio()
    assert rec["outcome"] == "audio" and rec["enabled"] is False and rec["latency_ms"] == 30000


def test_disabled_still_returns_no_reply_and_cancel_records(clk):
    wd = WaitWatchdog(Limits(enabled=False), clock=clk)
    wd.user_speech()
    assert wd.turn_complete()["outcome"] == "no_reply"
    wd.user_speech()
    assert wd.cancel("session")["cancel_reason"] == "session"


# ── robustness ───────────────────────────────────────────────────────────────
def test_events_never_raise_even_if_the_clock_does():
    def boom():
        raise RuntimeError("clock")

    wd = WaitWatchdog(LIM, clock=boom)
    wd.user_speech(); wd.user_text(); wd.tool_started(); wd.tool_finished()
    wd.phrase_played("voice")
    assert wd.model_audio() is None and wd.turn_complete() is None and wd.cancel("x") is None
    assert wd.tick() == ([], None)
