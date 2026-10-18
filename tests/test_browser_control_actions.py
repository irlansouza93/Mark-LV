"""browser_control: action names the model invents, and selectors from an older web.

Seen in the real app on 2026-10-17: the model called action "open_tab" (the tool
calls it new_tab) and got "Unknown browser action" — after the automation browser
had already been launched for nothing; and `type` with `input[name=q]` on Google
hung 30 s in Locator.clear, because Google's search box has been a <textarea>
since 2023.
"""
import pytest

pytest.importorskip("playwright")

from actions import browser_control as bc


def test_common_synonyms_map_to_real_actions():
    assert bc._canonical_action("open_tab") == "new_tab"
    assert bc._canonical_action(" Open-Tab ") == "new_tab"
    assert bc._canonical_action("navigate") == "go_to"
    assert bc._canonical_action("type_text") == "type"
    assert bc._canonical_action("click") == "click"


def test_unknown_action_fails_before_any_browser_starts(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not start a browser session for an unknown action")
    monkeypatch.setattr(bc._registry, "get", boom)
    out = bc.browser_control({"action": "teleport"})
    assert out.startswith("Unknown browser action: 'teleport'")
    assert "new_tab" in out     # the model gets the real names back


def test_open_tab_reaches_the_new_tab_path(monkeypatch):
    seen = []
    monkeypatch.setattr(bc, "_open_native", lambda url, b: seen.append(url) or f"Opened {url}")
    monkeypatch.setattr(bc._registry, "has", lambda b=None: False)
    out = bc.browser_control({"action": "open_tab", "url": "https://example.com"})
    assert seen == ["https://example.com"]
    assert out.startswith("Opened")


def test_input_selector_also_matches_textarea():
    assert bc._widen_selector("input[name=q]") == ":is(input,textarea)[name=q]"
    assert bc._widen_selector("input#search") == ":is(input,textarea)#search"


def test_other_selectors_are_left_alone():
    for sel in ("textarea[name=q]", "#q", "button.submit", "inputs", "input, select"):
        assert bc._widen_selector(sel) == sel, sel
