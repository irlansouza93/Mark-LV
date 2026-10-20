"""memory/config_manager.get_wait_cue — defaults, ranges and ordering of the wait-cue limits.

load_api_keys is swapped for a dict: no disk, no network."""
import pytest

from memory import config_manager as cm

DEFAULTS = {"enabled": True, "thinking_ms": 1500, "phrase_ms": 4000, "give_up_ms": 20000}


@pytest.fixture
def cfg(monkeypatch):
    box = {}
    monkeypatch.setattr(cm, "load_api_keys", lambda: box)
    return box


def test_missing_key_gives_the_defaults_with_the_net_on(cfg):
    assert cm.get_wait_cue() == DEFAULTS


def test_non_dict_value_gives_the_defaults(cfg):
    cfg["wait_cue"] = "yes please"
    assert cm.get_wait_cue() == DEFAULTS


def test_values_inside_the_ranges_are_kept(cfg):
    cfg["wait_cue"] = {"thinking_ms": 2000, "phrase_ms": 6000, "give_up_ms": 30000}
    assert cm.get_wait_cue() == {"enabled": True, "thinking_ms": 2000, "phrase_ms": 6000, "give_up_ms": 30000}


@pytest.mark.parametrize("given, expected", [
    ({"thinking_ms": 10}, 300),
    ({"thinking_ms": 99999}, 10000),
])
def test_thinking_is_clamped_to_its_range(cfg, given, expected):
    cfg["wait_cue"] = {**given, "phrase_ms": 30000, "give_up_ms": 120000}
    assert cm.get_wait_cue()["thinking_ms"] == expected


def test_phrase_and_give_up_are_clamped_to_their_ranges(cfg):
    cfg["wait_cue"] = {"phrase_ms": 5, "give_up_ms": 5}
    got = cm.get_wait_cue()
    assert got["phrase_ms"] >= 1000 and got["give_up_ms"] >= 5000
    cfg["wait_cue"] = {"phrase_ms": 10**9, "give_up_ms": 10**9}
    got = cm.get_wait_cue()
    assert got["phrase_ms"] == 30000 and got["give_up_ms"] == 120000


@pytest.mark.parametrize("bad", ["abc", None, [1], {"x": 1}])
def test_invalid_type_falls_back_to_the_default(cfg, bad):
    cfg["wait_cue"] = {"thinking_ms": bad, "phrase_ms": bad, "give_up_ms": bad}
    assert cm.get_wait_cue() == DEFAULTS


def test_phrase_not_after_thinking_is_pushed_500_ms_past_it(cfg):
    cfg["wait_cue"] = {"thinking_ms": 3000, "phrase_ms": 2000}
    got = cm.get_wait_cue()
    assert got["phrase_ms"] == 3500


def test_give_up_not_after_phrase_is_pushed_1000_ms_past_it(cfg):
    cfg["wait_cue"] = {"phrase_ms": 8000, "give_up_ms": 6000}
    got = cm.get_wait_cue()
    assert got["give_up_ms"] == 9000


def test_ordering_cascades_from_thinking_to_give_up(cfg):
    cfg["wait_cue"] = {"thinking_ms": 10000, "phrase_ms": 1000, "give_up_ms": 5000}
    got = cm.get_wait_cue()
    assert (got["thinking_ms"], got["phrase_ms"], got["give_up_ms"]) == (10000, 10500, 11500)


def test_enabled_false_is_respected(cfg):
    cfg["wait_cue"] = {"enabled": False}
    got = cm.get_wait_cue()
    assert got["enabled"] is False and got["thinking_ms"] == 1500
