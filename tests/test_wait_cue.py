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


# ── phrases (spec 002 US2): cache, tone, prefetch — never the network ────────
import random
import threading
import wave

import numpy as np

from core import voice_preview, wait_cue


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_preview, "CACHE_DIR", tmp_path)
    wait_cue._last_variant.clear()
    wait_cue._live_tried.clear()
    wait_cue._tts_tried.clear()
    wait_cue._inflight.clear()
    return tmp_path


def _put_wav(path, samples, rate):
    voice_preview._write_wav(path, np.asarray(samples, dtype=np.int16), rate)


def _speech(seconds, lead=0.0, trail=0.0, level=3000):
    """int16 @ 24 kHz: ``seconds`` of a loud tone between ``lead``/``trail`` seconds of
    near-silence (amplitude 20, i.e. < 2% of the peak) — what a TTS reply looks like."""
    n = int(wait_cue.RATE * seconds)
    body = (np.sin(np.arange(n) * 0.3) * level).astype(np.int16)
    pad = lambda sec: np.full(int(wait_cue.RATE * sec), 20, dtype=np.int16)
    return np.concatenate([pad(lead), body, pad(trail)])


def test_phrase_path_name_is_versioned_indexed_and_tagged_with_its_source(cache):
    assert wait_cue.phrase_path("hold", 2, "Charon", "pt", "tts") == cache / "wait_v4_tts_hold_2_Charon_pt.wav"
    assert wait_cue.phrase_path("hold", 2, "Charon", "pt", "live") == cache / "wait_v4_live_hold_2_Charon_pt.wav"
    assert wait_cue.phrase_path("hold", 2, "Charon", "pt") == wait_cue.phrase_path("hold", 2, "Charon", "pt", "tts")


def test_load_phrase_none_without_file_and_int16_24k_with_it(cache):
    assert wait_cue.load_phrase("hold", 0, "Charon", "pt") is None
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt"), [1, -2, 3, 4], 24000)
    pcm = wait_cue.load_phrase("hold", 0, "Charon", "pt")
    assert pcm.dtype == np.int16 and pcm.tolist() == [1, -2, 3, 4]


def test_load_phrase_resamples_16k_to_24k(cache):
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt"), np.arange(1600) % 100, 16000)
    pcm = wait_cue.load_phrase("hold", 0, "Charon", "pt")
    assert pcm.dtype == np.int16 and abs(len(pcm) - 2400) <= 1      # 0.1 s at 24 kHz


def test_load_phrase_corrupt_file_is_none_not_an_error(cache):
    wait_cue.phrase_path("hold", 0, "Charon", "pt").write_bytes(b"not a wav")
    assert wait_cue.load_phrase("hold", 0, "Charon", "pt") is None


def test_tone_is_int16_24k_about_300ms_audible_and_soft():
    t = wait_cue.tone()
    assert t.dtype == np.int16 and wait_cue.RATE == 24000
    assert 0.25 <= len(t) / wait_cue.RATE <= 0.35
    peak = int(np.abs(t.astype(np.int32)).max())
    assert 0 < peak <= 0.3 * 32767


def test_pcm_for_voice_with_cache_and_tone_without(cache):
    pcm, how = wait_cue.pcm_for("hold", "Charon", "pt")
    assert how == "tone" and pcm.dtype == np.int16 and len(pcm) == len(wait_cue.tone())
    _put_wav(wait_cue.phrase_path("hold", 3, "Charon", "pt"), [5, 6, 7], 24000)
    pcm, how = wait_cue.pcm_for("hold", "Charon", "pt")
    assert how == "voice" and pcm.tolist() == [5, 6, 7]


def test_pcm_for_never_raises_on_unknown_kind(cache):
    pcm, how = wait_cue.pcm_for("nonsense", "Charon", "pt")
    assert how == "tone"


def test_pcm_for_ignores_the_version_1_and_2_files(cache):
    _put_wav(cache / "wait_hold_Charon_pt.wav", [5, 6, 7], 24000)       # the old names
    _put_wav(cache / "wait_v2_hold_0_Charon_pt.wav", [5, 6, 7], 24000)
    assert wait_cue.pcm_for("hold", "Charon", "pt")[1] == "tone"


def test_pcm_for_picks_among_cached_variants_and_never_repeats_the_last(cache):
    for i in (0, 2, 4):
        _put_wav(wait_cue.phrase_path("hold", i, "Charon", "pt"), [i + 10], 24000)
    rng = random.Random(7)
    seen = []
    for _ in range(40):
        pcm, how = wait_cue.pcm_for("hold", "Charon", "pt", rng=rng)
        assert how == "voice"
        seen.append(int(pcm[0]))
    assert set(seen) == {10, 12, 14}                      # the three cached ones, no others
    assert all(a != b for a, b in zip(seen, seen[1:]))    # never the same twice in a row


def test_pcm_for_remembers_the_last_variant_per_kind(cache):
    for kind in ("hold", "giveup"):
        for i in (0, 1):
            _put_wav(wait_cue.phrase_path(kind, i, "Charon", "pt"), [i + 10], 24000)
    rng = random.Random(1)
    a = int(wait_cue.pcm_for("hold", "Charon", "pt", rng=rng)[0][0])
    wait_cue.pcm_for("giveup", "Charon", "pt", rng=rng)   # another kind must not disturb "hold"
    b = int(wait_cue.pcm_for("hold", "Charon", "pt", rng=rng)[0][0])
    assert a != b


def test_pcm_for_single_cached_variant_repeats(cache):
    _put_wav(wait_cue.phrase_path("giveup", 1, "Charon", "pt"), [9], 24000)
    for _ in range(3):
        pcm, how = wait_cue.pcm_for("giveup", "Charon", "pt")
        assert how == "voice" and pcm.tolist() == [9]


def test_texts_cover_the_seven_picker_languages_with_enough_words():
    langs = set(voice_preview._PHRASES)
    assert langs == {"pt", "en", "es", "fr", "de", "it", "tr"} == set(wait_cue.BCP47)
    assert wait_cue.BCP47["pt"] == "pt-BR" and wait_cue.BCP47["en"] == "en-US"
    for kind, want in (("hold", 3), ("giveup", 2)):
        assert set(wait_cue.TEXTS[kind]) == langs
        for l in langs:
            variants = wait_cue.TEXTS[kind][l]
            assert isinstance(variants, list) and len(variants) >= want
            # Under five words the TTS drifts to the wrong accent (R4b: "Um instante." -> English).
            assert all(len(v.split()) >= 5 for v in variants), (kind, l)
            assert len(set(variants)) == len(variants)
    assert len(wait_cue.TEXTS["hold"]["pt"]) == 5 and len(wait_cue.TEXTS["giveup"]["pt"]) == 3
    assert wait_cue.TEXTS["hold"]["pt"] == [
        "Só um momento, por favor.", "Um instante, já vejo isso.", "Deixa eu ver isso aqui.",
        "Só um segundo, já vejo.", "Peraí, já vou ver isso."]
    # R3 (T030): a wait phrase is short so it ends before the answer starts
    for l in langs:
        assert all(len(v.split()) <= 7 for v in wait_cue.TEXTS["hold"][l]), l
    assert wait_cue.TEXTS["giveup"]["pt"][0] == "Desculpe, não consegui responder. Pode repetir?"


def _all_texts(lang):
    return [t for kind in ("hold", "giveup") for t in wait_cue.TEXTS[kind][lang]]


def _synth_ok(calls=None, seconds=1.5):
    def synth(voice, lang, text=None, style=None, language_code=None):
        if calls is not None:
            calls.append({"voice": voice, "lang": lang, "text": text, "style": style,
                          "language_code": language_code})
        return _speech(seconds), 24000
    return synth


def test_prefetch_generates_every_variant_without_style_and_with_bcp47(cache):
    calls = []
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls)).join(5)
    assert sorted(c["text"] for c in calls) == sorted(_all_texts("pt"))
    assert all(c["style"] is None and c["language_code"] == "pt-BR" for c in calls)
    for kind in ("hold", "giveup"):
        for i in range(len(wait_cue.TEXTS[kind]["pt"])):
            assert wait_cue.phrase_path(kind, i, "Charon", "pt").exists()
    # all cached now: nothing to do, no thread
    assert wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls)) is None


def test_prefetch_generates_only_the_missing_variant(cache):
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok()).join(5)
    wait_cue.phrase_path("giveup", 1, "Charon", "pt").unlink()
    wait_cue._tts_tried.clear()               # a new run of the app
    calls = []
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls)).join(5)
    assert [c["text"] for c in calls] == [wait_cue.TEXTS["giveup"]["pt"][1]]
    assert wait_cue.phrase_path("giveup", 1, "Charon", "pt").exists()


def test_prefetch_runs_off_the_calling_thread_as_daemon(cache):
    seen = []

    def synth(*a, **k):
        seen.append(threading.current_thread())
        return _speech(1.5), 24000

    th = wait_cue.prefetch("Charon", "en", synth=synth)
    th.join(5)
    assert th.daemon and seen and all(t is not threading.current_thread() for t in seen)


def test_prefetch_synth_failure_reports_each_variant_and_leaves_no_partial_file(cache):
    errors = []

    def synth(*a, **k):
        raise RuntimeError("quota")

    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append((k, m)),
                      synth=synth).join(5)
    assert sorted(k for k, _ in errors) == ["giveup"] * 3 + ["hold"] * 5
    assert all("quota" in m for _, m in errors)
    assert list(cache.iterdir()) == []
    assert wait_cue.pcm_for("hold", "Charon", "pt")[1] == "tone"


def test_prefetch_one_failing_variant_does_not_stop_the_others(cache):
    errors = []
    first = wait_cue.TEXTS["hold"]["pt"][0]

    def synth(voice, lang, text=None, style=None, language_code=None):
        if text == first:
            raise RuntimeError("boom")
        return _speech(1.5), 24000

    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append(k),
                      synth=synth).join(5)
    assert errors == ["hold"]
    assert not wait_cue.phrase_path("hold", 0, "Charon", "pt").exists()
    assert wait_cue.phrase_path("hold", 1, "Charon", "pt").exists()
    assert wait_cue.phrase_path("giveup", 2, "Charon", "pt").exists()


def test_prefetch_without_on_error_swallows_failure(cache):
    wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: 1 / 0).join(5)


def test_prefetch_unknown_language_falls_back_to_english_text_and_us_english(cache):
    calls = []
    wait_cue.prefetch("Charon", "xx", synth=_synth_ok(calls)).join(5)
    assert wait_cue.TEXTS["hold"]["en"][0] in [c["text"] for c in calls]
    assert {c["language_code"] for c in calls} == {"en-US"}


# ── generated audio is validated before it is cached (R4b) ───────────────────
@pytest.mark.parametrize("kind,seconds,ok", [
    ("hold", 0.5, False), ("hold", 0.7, True), ("hold", 2.2, True), ("hold", 2.3, False),
    ("giveup", 0.5, False), ("giveup", 5.9, True), ("giveup", 6.3, False),
])
def test_prefetch_validates_speech_duration(cache, kind, seconds, ok):
    errors = []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append((k, m)),
                      synth=lambda *a, **k: (_speech(seconds), 24000)).join(5)
    written = wait_cue.phrase_path(kind, 0, "Charon", "pt").exists()
    bad = [m for k, m in errors if k == kind]
    assert written is ok and (not bad) is ok
    if not ok:
        assert bad[0].startswith("phrase audio ") and "outside" in bad[0]


def test_prefetch_trims_leading_and_trailing_silence_before_measuring_and_writing(cache):
    # 1.0 s of speech inside 3 s of silence each side: 7 s raw would fail "hold", 1 s is fine.
    wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: (_speech(1.0, 3.0, 3.0), 24000)).join(5)
    pcm = wait_cue.load_phrase("hold", 0, "Charon", "pt")
    assert pcm is not None and abs(len(pcm) / wait_cue.RATE - 1.0) < 0.05
    assert int(np.abs(pcm[:200]).max()) > 600            # starts on speech, not on the pad


def test_prefetch_all_silence_is_rejected(cache):
    errors = []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append(k),
                      synth=lambda *a, **k: (np.zeros(48000, dtype=np.int16), 24000)).join(5)
    assert len(errors) == 8 and list(cache.iterdir()) == []


def test_prefetch_validation_is_in_seconds_not_samples(cache):
    # 1.5 s at 16 kHz is still 1.5 s.
    pcm16 = (np.sin(np.arange(24000) * 0.3) * 3000).astype(np.int16)
    wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: (pcm16, 16000)).join(5)
    assert wait_cue.load_phrase("hold", 0, "Charon", "pt") is not None


# ── review fixes ─────────────────────────────────────────────────────────────
def test_source_is_public_and_follows_how_the_episode_opened(clk):
    wd = WaitWatchdog(LIM, clock=clk)
    wd.user_speech()
    assert wd.source == "voice"
    wd.cancel("x")
    wd.user_text()
    assert wd.source == "text"


def test_user_speech_while_waiting_rearms_thinking_but_not_the_phrase(wd, clk):
    wd.user_speech()
    acts, _ = run(wd, clk, 1.6)
    assert acts == ["thinking"] and wd.thinking_shown
    wd.user_speech()                       # the user spoke again: new anchor
    assert not wd.thinking_shown
    acts, _ = run(wd, clk, 1.6)
    assert acts == ["thinking"]            # shown again from the new anchor
    acts, _ = run(wd, clk, 3.0)
    assert acts.count("phrase") == 1       # the phrase is still once per episode
    acts, _ = run(wd, clk, 1.0)
    assert "phrase" not in acts
    wd.model_audio()
    assert wd.turn_complete() is None


def test_record_keeps_thinking_true_even_after_rearming(wd, clk):
    wd.user_speech()
    run(wd, clk, 1.6)
    wd.user_speech()
    assert wd.turn_complete()["thinking"] is True


def test_load_phrase_empty_wav_is_none(cache):
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt"), [], 24000)
    assert wait_cue.load_phrase("hold", 0, "Charon", "pt") is None


def test_prefetch_regenerates_unreadable_or_empty_cache_files(cache):
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok()).join(5)
    wait_cue.phrase_path("hold", 0, "Charon", "pt").write_bytes(b"garbage")
    _put_wav(wait_cue.phrase_path("giveup", 0, "Charon", "pt"), [], 24000)
    wait_cue._tts_tried.clear()               # a new run of the app
    calls = []
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls)).join(5)
    assert len(calls) == 2
    assert wait_cue.load_phrase("hold", 0, "Charon", "pt") is not None
    assert wait_cue.load_phrase("giveup", 0, "Charon", "pt") is not None


def test_prefetch_empty_pcm_is_an_error_and_writes_nothing(cache):
    errors = []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append(k),
                      synth=lambda *a, **k: (np.zeros(0, dtype=np.int16), 24000)).join(5)
    assert len(errors) == 8 and set(errors) == {"giveup", "hold"}
    assert list(cache.iterdir()) == []


# ── a transcript that arrives with the answer is not a latency (R4b) ─────────
def test_late_transcript_closes_as_cancelled_without_latency(wd, clk):
    wd.user_speech()
    clk.adv(0.001)                                   # the 1 ms seen in the real test
    rec = wd.model_audio()
    assert rec["outcome"] == "cancelled" and rec["cancel_reason"] == "late_transcript"
    assert rec["latency_ms"] is None
    assert wd.turn_complete() is None                # the exchange continues as usual


def test_latency_just_over_the_threshold_still_counts(wd, clk):
    wd.user_speech()
    clk.adv(wait_cue.LATE_TRANSCRIPT_MS / 1000 + 0.01)
    rec = wd.model_audio()
    assert rec["outcome"] == "audio" and rec["latency_ms"] >= wait_cue.LATE_TRANSCRIPT_MS


def test_late_transcript_stays_out_of_the_percentiles(wd, clk):
    from core import llm_usage
    wd.user_speech()
    clk.adv(0.001)
    rec = {"kind": "voice_wait", **wd.model_audio()}
    got = llm_usage.wait_summary([rec])
    assert got["outcomes"]["cancelled"] == 1 and got["with_latency"] == 0


# ── phrases captured from the Live model itself (R4c) — fake connection, no network ──
import asyncio
from types import SimpleNamespace as NS


def _resp(data=None, text=None, done=False, usage=None):
    sc = NS(output_transcription=NS(text=text) if text is not None else None, turn_complete=done)
    return NS(data=data, server_content=sc, usage_metadata=usage)


def _turn(text_heard, seconds=1.5, usage=None):
    """What the Live model sends for one line: audio in two chunks, the transcript in two
    chunks, then turn_complete."""
    pcm = _speech(seconds).tobytes()
    half = len(pcm) // 2
    words = text_heard.split(" ")
    mid = len(words) // 2
    return [_resp(data=pcm[:half]), _resp(text=" ".join(words[:mid]) + " "),
            _resp(data=pcm[half:]), _resp(text=" ".join(words[mid:])),
            _resp(done=True, usage=usage)]


class FakeLive:
    """connect(model, config) -> async context manager; ``script(text)`` gives the list of
    responses for a line (or "hang", or an exception to raise)."""

    def __init__(self, script, connect_error=None):
        self.script, self.connect_error = script, connect_error
        self.connects, self.sent = [], []
        self.exited = False

    def __call__(self, model, config):
        self.connects.append((model, config))
        outer = self

        class CM:
            async def __aenter__(self_):
                if outer.connect_error:
                    raise outer.connect_error
                return NS(send_client_content=outer._send, receive=outer._receive)

            async def __aexit__(self_, *a):
                outer.exited = True
                return False
        return CM()

    async def _send(self, *, turns, turn_complete):
        self.sent.append((turns, turn_complete))

    async def _receive(self):
        text = self.sent[-1][0]["parts"][0]["text"]
        out = self.script(text)
        if isinstance(out, Exception):
            raise out
        if out == "hang":
            await asyncio.sleep(60)
        for r in out:
            yield r


def _echo(text):
    return _turn(text)


def test_live_capture_one_connection_for_all_lines_and_returns_pcm_and_transcript():
    fake = FakeLive(_echo)
    texts = ["Só um momento, já vejo isso.", "Estou cuidando disso, só um segundo."]
    got = wait_cue.live_capture(texts, model="live-m", voice="Charon", lang="pt", connect=fake)
    assert len(fake.connects) == 1 and fake.connects[0][0] == "live-m" and fake.exited
    assert [s[0] for s in fake.sent] == [{"role": "user", "parts": [{"text": t}]} for t in texts]
    assert all(s[1] is True for s in fake.sent)
    for (pcm, heard, err), t in zip(got, texts):
        assert err is None and pcm.dtype == np.int16 and len(pcm) == int(24000 * 1.5)
        assert wait_cue._matches(t, heard)


def test_live_capture_config_has_voice_transcription_and_a_speech_engine_prompt_no_tools():
    fake = FakeLive(_echo)
    wait_cue.live_capture(["Só um momento, já vejo isso."], model="m", voice="Laomedeia",
                          lang="pt", connect=fake)
    cfg = fake.connects[0][1]
    assert cfg["response_modalities"] == ["AUDIO"]
    assert cfg["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Laomedeia"
    assert "language_code" not in cfg["speech_config"] and "tools" not in cfg
    assert cfg["output_audio_transcription"] == {}
    si = cfg["system_instruction"]
    assert "speech engine" in si and "word for word" in si and "Portuguese" in si
    assert "natural, brisk pace" in si


def test_live_capture_logs_one_oneshot_usage_row_per_line(monkeypatch):
    from core import llm_usage
    rows = []
    monkeypatch.setattr(llm_usage, "record_oneshot", lambda *a, **k: rows.append(a))
    um = NS(total_token_count=42)
    fake = FakeLive(lambda t: _turn("so um momento", usage=um))
    wait_cue.live_capture(["a", "b"], model="live-m", voice="Charon", lang="pt", connect=fake)
    assert len(rows) == 2
    model, tier, result, latency, usage = rows[0]
    assert model == "live-m" and tier is None and result == "ok"
    assert isinstance(latency, int) and usage is um


def test_live_capture_connect_failure_marks_every_line_and_never_raises():
    fake = FakeLive(_echo, connect_error=RuntimeError("429 quota"))
    got = wait_cue.live_capture(["a", "b"], model="m", voice="V", lang="pt", connect=fake)
    assert [g[0] for g in got] == [None, None]
    assert all(g[2].startswith("connect:") and "429" in g[2] for g in got)


def test_live_capture_timeout_on_a_line_skips_the_rest_but_keeps_the_earlier_ones():
    fake = FakeLive(lambda t: "hang" if t == "b" else _echo(t))
    got = wait_cue.live_capture(["a", "b", "c"], model="m", voice="V", lang="pt",
                                connect=fake, timeout_s=0.2)
    assert got[0][2] is None and got[0][0] is not None
    assert got[1][0] is None and "timeout" in got[1][2]
    assert got[2][0] is None and got[2][2]            # not sent on a session left mid-turn
    assert len(fake.sent) == 2


def test_live_capture_no_audio_is_an_error_for_that_line():
    fake = FakeLive(lambda t: [_resp(text="hola"), _resp(done=True)])
    (pcm, heard, err), = wait_cue.live_capture(["hola"], model="m", voice="V", lang="es", connect=fake)
    assert pcm is None and heard == "hola" and "no audio" in err


@pytest.mark.parametrize("expected,heard,ok", [
    ("Só um momento, já vejo isso.", "so um momento ja vejo isso", True),
    ("Só um momento, já vejo isso.", "Só um momento, já vejo isso.", True),
    ("Desculpe, não consegui responder. Pode repetir?", "Desculpe não consegui responder pode repetir", True),
    ("Só um momento, já vejo isso.", "A momento", False),
    ("Só um momento, já vejo isso.", "Estou cuidando disso, só um segundo.", False),
    ("Só um momento, já vejo isso.", "", False),
])
def test_matches_normalises_and_needs_85_percent_similarity(expected, heard, ok):
    assert wait_cue._matches(expected, heard) is ok


# prefetch with the Live model first, TTS after
def _live_for(heard=None, seconds=1.5):
    """A fake Live that repeats every requested line (or ``heard(line)``)."""
    return FakeLive(lambda t: _turn((heard(t) if heard else t), seconds=seconds))


def test_pcm_for_prefers_live_over_tts_and_falls_back(cache):
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt", "tts"), [1], 24000)
    assert wait_cue.pcm_for("hold", "Charon", "pt")[0].tolist() == [1]
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt", "live"), [2], 24000)
    assert wait_cue.pcm_for("hold", "Charon", "pt")[0].tolist() == [2]
    wait_cue.phrase_path("hold", 0, "Charon", "pt", "live").write_bytes(b"corrupt")
    assert wait_cue.pcm_for("hold", "Charon", "pt")[0].tolist() == [1]      # live unreadable -> tts


def test_hold_phrases_are_short_but_giveup_keeps_its_length():
    assert wait_cue._DURATION_S == {"hold": (0.6, 2.2), "giveup": (0.6, 6.0)}


def test_prefetch_with_live_model_writes_live_files_and_skips_tts(cache):
    fake, calls = _live_for(), []
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls), live_model="live-m", connect=fake).join(5)
    assert len(fake.connects) == 1 and calls == []
    for kind in ("hold", "giveup"):
        for i in range(len(wait_cue.TEXTS[kind]["pt"])):
            assert wait_cue.phrase_path(kind, i, "Charon", "pt", "live").exists()
            assert not wait_cue.phrase_path(kind, i, "Charon", "pt", "tts").exists()
    # everything is on disk now
    assert wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls), live_model="live-m", connect=fake) is None


def test_prefetch_without_live_model_is_tts_only_as_before(cache):
    calls = []
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls)).join(5)
    assert len(calls) == 8
    assert wait_cue.phrase_path("hold", 0, "Charon", "pt", "tts").exists()
    assert not wait_cue.phrase_path("hold", 0, "Charon", "pt", "live").exists()


def test_prefetch_live_mismatch_is_reported_and_that_variant_falls_to_tts(cache):
    first = wait_cue.TEXTS["hold"]["pt"][0]
    fake = _live_for(heard=lambda t: "A momento" if t == first else t)
    errors, calls = [], []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append((k, m)),
                      synth=_synth_ok(calls), live_model="m", connect=fake).join(5)
    assert [k for k, _ in errors] == ["hold"]
    assert not wait_cue.phrase_path("hold", 0, "Charon", "pt", "live").exists()
    assert wait_cue.phrase_path("hold", 0, "Charon", "pt", "tts").exists()
    assert [c["text"] for c in calls] == [first]


def test_prefetch_live_audio_outside_duration_is_rejected(cache):
    fake = _live_for(seconds=0.2)
    errors = []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append(k),
                      synth=_synth_ok(), live_model="m", connect=fake).join(5)
    assert len(errors) == 8
    assert not list(cache.glob("wait_v4_live_*"))
    assert len(list(cache.glob("wait_v4_tts_*"))) == 8


def test_prefetch_connect_failure_reports_live_once_and_uses_tts(cache):
    fake = FakeLive(_echo, connect_error=RuntimeError("quota"))
    errors, calls = [], []
    wait_cue.prefetch("Charon", "pt", on_error=lambda k, m: errors.append((k, m)),
                      synth=_synth_ok(calls), live_model="m", connect=fake).join(5)
    assert [k for k, _ in errors] == ["live"] and "quota" in errors[0][1]
    assert len(calls) == 8


def test_prefetch_tries_live_once_per_run_for_each_voice_and_language(cache):
    fake = FakeLive(_echo, connect_error=RuntimeError("quota"))
    for _ in range(2):
        th = wait_cue.prefetch("Charon", "pt", synth=_synth_ok(), live_model="m", connect=fake)
        if th:
            th.join(5)
        for f in cache.glob("wait_v4_tts_*"):
            f.unlink()                      # force "missing" again; the Live attempt is already spent
        wait_cue._tts_tried.clear()
    assert len(fake.connects) == 1
    wait_cue.prefetch("Charon", "en", synth=_synth_ok(), live_model="m", connect=fake).join(5)
    assert len(fake.connects) == 2


def test_prefetch_asks_live_only_for_variants_without_a_live_file(cache):
    _put_wav(wait_cue.phrase_path("hold", 0, "Charon", "pt", "live"), _speech(1.0), 24000)
    fake = _live_for()
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok(), live_model="m", connect=fake).join(5)
    asked = [s[0]["parts"][0]["text"] for s in fake.sent]
    assert wait_cue.TEXTS["hold"]["pt"][0] not in asked and len(asked) == 7


def test_prefetch_live_failure_leaves_existing_tts_files_alone(cache):
    wait_cue.prefetch("Charon", "pt", synth=_synth_ok()).join(5)        # tts cache filled
    fake = FakeLive(_echo, connect_error=RuntimeError("quota"))
    calls = []
    th = wait_cue.prefetch("Charon", "pt", synth=_synth_ok(calls), live_model="m", connect=fake)
    th.join(5)
    assert calls == [] and len(list(cache.glob("wait_v4_tts_*"))) == 8
    assert wait_cue.pcm_for("hold", "Charon", "pt")[1] == "voice"


# ── the phrase must not reach the model through the mic (R3b, T031) ──────────
def test_cue_mute_is_duration_plus_output_latency_plus_margin():
    assert wait_cue.CUE_MUTE_MARGIN_S == 0.6
    assert wait_cue.cue_mute_s(1.5, 0.213) == pytest.approx(1.5 + 0.213 + 0.6)


@pytest.mark.parametrize("lat", [None, -0.1, float("nan"), "x"])
def test_cue_mute_uses_a_conservative_latency_when_the_measure_is_unusable(lat):
    assert wait_cue.cue_mute_s(1.0, lat) == pytest.approx(1.0 + 0.25 + 0.6)


def test_pcm_for_with_text_returns_the_phrase_that_was_chosen(cache):
    _put_wav(wait_cue.phrase_path("hold", 2, "Charon", "pt", "live"), [5, 6], 24000)
    pcm, how, text = wait_cue.pcm_for("hold", "Charon", "pt", with_text=True)
    assert how == "voice" and text == wait_cue.TEXTS["hold"]["pt"][2] == "Deixa eu ver isso aqui."
    assert wait_cue.pcm_for("hold", "Charon", "pt")[1] == "voice"         # two values, as before
    _, how, text = wait_cue.pcm_for("giveup", "Charon", "pt", with_text=True)
    assert (how, text) == ("tone", "")


@pytest.mark.parametrize("heard,ok", [
    ("ver isso aqui", True),                              # the tail of the phrase (3 words)
    ("Deixa eu ver isso aqui", True),
    ("isso aqui", False),                                 # two words are too common in real speech
    ("deixa eu ver isso", True),
    ("qual a previsão do tempo", False),
    ("", False),
    ("aqui", False),                                      # one word contained is not enough
])
def test_echo_suspect_recognises_our_own_phrase_coming_back(heard, ok):
    assert wait_cue.echo_suspect(heard, "Deixa eu ver isso aqui.") is ok


def test_echo_suspect_never_for_the_tone_or_the_same_text_as_the_question():
    assert wait_cue.echo_suspect("qual a previsão do tempo", "") is False
    assert wait_cue.echo_suspect("qual a previsão do tempo", "qual a previsão do tempo") is True


def test_record_carries_echo_suspect_false_by_default_and_true_when_marked(wd, clk):
    wd.user_speech()
    clk.adv(1.0)
    assert wd.model_audio()["echo_suspect"] is False
    wd.turn_complete()
    wd.user_speech()
    clk.adv(5.0)
    wd.tick()
    wd.phrase_heard_back()
    clk.adv(1.0)
    assert wd.model_audio()["echo_suspect"] is True
    wd.turn_complete()
    wd.user_speech()                                      # next episode starts clean
    clk.adv(1.0)
    assert wd.model_audio()["echo_suspect"] is False


def test_echo_suspect_reaches_every_kind_of_record(wd, clk):
    wd.user_speech()
    wd.phrase_heard_back()
    assert wd.turn_complete()["echo_suspect"] is True
    wd.user_speech()
    assert wd.cancel("interrupt")["echo_suspect"] is False
    wd.user_speech()
    wd.phrase_heard_back()
    clk.adv(25.0)
    assert wd.tick()[1]["echo_suspect"] is True


def test_phrase_heard_back_outside_an_episode_is_a_no_op(wd):
    wd.phrase_heard_back()


# summary and report
def test_wait_summary_counts_echo_suspect_episodes():
    from core import llm_usage
    base = {"kind": "voice_wait", "outcome": "audio", "latency_ms": 1000}
    got = llm_usage.wait_summary([dict(base, echo_suspect=True), dict(base, echo_suspect=False),
                                  dict(base), dict(base, echo_suspect=True)])
    assert got["echo_suspect"] == 2
    assert llm_usage.wait_summary([])["echo_suspect"] == 0


def test_usage_report_shows_echo_suspect_only_when_there_is_one():
    from tools import usage_report
    base = {"kind": "voice_wait", "outcome": "audio", "latency_ms": 1000}
    from core import llm_usage
    quiet = usage_report._wait_lines(llm_usage.wait_summary([base]), None)
    assert not any("eco suspeito" in l for l in quiet)
    loud = usage_report._wait_lines(llm_usage.wait_summary([dict(base, echo_suspect=True)] * 3), None)
    line = [l for l in loud if "rede agiu" in l][0]
    assert line.endswith(" · eco suspeito 3")


# ── review fixes: one prefetch at a time, one TTS attempt per run (T031 review) ──
def test_prefetch_returns_none_while_a_thread_for_the_pair_is_alive(cache):
    gate, started = threading.Event(), threading.Event()

    def slow(*a, **k):
        started.set()
        gate.wait(5)
        return _speech(1.5), 24000

    th = wait_cue.prefetch("Charon", "pt", synth=slow)
    assert started.wait(5)
    # a reconnection while the first run is still capturing/synthesising
    assert wait_cue.prefetch("Charon", "pt", synth=_synth_ok()) is None
    assert wait_cue.prefetch("Charon", "pt", synth=_synth_ok(), live_model="m",
                             connect=FakeLive(_echo)) is None
    other = wait_cue.prefetch("Charon", "en", synth=_synth_ok())       # another pair is independent
    assert other is not None
    other.join(5)
    gate.set()
    th.join(5)
    assert ("Charon", "pt") not in wait_cue._inflight and ("Charon", "en") not in wait_cue._inflight


def test_prefetch_inflight_is_released_even_when_the_work_blows_up(cache):
    th = wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: 1 / 0,
                           on_error=lambda *a: 1 / 0)
    th.join(5)
    assert not wait_cue._inflight


def test_prefetch_tts_is_tried_once_per_run_for_each_pair(cache):
    calls = []
    wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: calls.append(1) or 1 / 0).join(5)
    assert len(calls) == 8
    # next connection of the same run: nothing is hammered, no thread
    assert wait_cue.prefetch("Charon", "pt", synth=lambda *a, **k: calls.append(1) or 1 / 0) is None
    assert len(calls) == 8
    wait_cue.prefetch("Charon", "en", synth=lambda *a, **k: calls.append(1) or 1 / 0).join(5)
    assert len(calls) == 8 + 5
