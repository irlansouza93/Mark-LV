"""Spec 003 (T016): confirm.request(report=True) hands the outcome back to the model.

No Qt, no main.py: show/hide/deliver are fakes and every wait is an Event.
"""
import threading

import pytest

from core import confirm

WAIT = 2.0


@pytest.fixture
def gate(monkeypatch):
    got = []
    ev = threading.Event()

    def deliver(text):
        got.append(text)
        ev.set()

    confirm._pending = None
    confirm.bind(lambda t, d: None, lambda: None, deliver=deliver)
    yield got, ev
    confirm._pending = None
    confirm.bind(None, None)


def test_confirmed_result_is_delivered(gate):
    got, ev = gate
    confirm.request("k", "Run it", "d", lambda: "out", report=True)
    confirm.resolve(True)
    assert ev.wait(WAIT)
    assert got == ["[CONFIRMED_RESULT] Run it: out"]


def test_cancel_is_delivered(gate):
    got, ev = gate
    confirm.request("k", "Run it", "d", lambda: "out", report=True)
    confirm.resolve(False)
    assert ev.wait(WAIT)
    assert got == ["[CONFIRMED_RESULT] Run it: the user cancelled it."]


def test_expiry_is_delivered_and_nothing_runs(gate, monkeypatch):
    got, ev = gate
    monkeypatch.setattr(confirm, "TIMEOUT_SECONDS", 0.05)
    ran = []
    confirm.request("k", "Run it", "d", lambda: ran.append(1) or "out", report=True)
    assert ev.wait(WAIT)
    assert got == ["[CONFIRMED_RESULT] Run it: the confirmation expired, nothing was run."]
    assert not ran and confirm.pending_title() == ""


def test_replaced_request_reports_to_the_first(gate):
    got, ev = gate
    confirm.request("a", "First", "d", lambda: "x", report=True)
    confirm.request("b", "Second", "d", lambda: "y", report=True)
    assert ev.wait(WAIT)
    assert got == ["[CONFIRMED_RESULT] First: replaced by a newer request, nothing was run."]
    assert confirm.pending_title() == "Second"


def test_long_result_is_clipped(gate):
    got, ev = gate
    confirm.request("k", "T", "d", lambda: "x" * 9000, report=True)
    confirm.resolve(True)
    assert ev.wait(WAIT)
    assert got[0].startswith("[CONFIRMED_RESULT] T: ")
    assert len(got[0]) <= len("[CONFIRMED_RESULT] T: ") + 4000


def test_timer_is_cancelled_when_resolved(gate, monkeypatch):
    got, ev = gate
    monkeypatch.setattr(confirm, "TIMEOUT_SECONDS", 0.1)
    confirm.request("k", "T", "d", lambda: "out", report=True)
    confirm.resolve(True)
    assert ev.wait(WAIT)
    ev.clear()
    assert not ev.wait(0.3)          # no late "expired" message
    assert got == ["[CONFIRMED_RESULT] T: out"]


def test_report_false_delivers_nothing(gate):
    got, ev = gate
    done = threading.Event()
    confirm.request("k", "T", "d", lambda: done.set() or "out")
    confirm.resolve(True)
    assert done.wait(WAIT)
    assert not ev.wait(0.2) and got == []


def test_no_deliver_bound_is_todays_behaviour():
    confirm._pending = None
    done = threading.Event()
    confirm.bind(lambda t, d: None, lambda: None)
    try:
        confirm.request("k", "T", "d", lambda: done.set() or "out", report=True)
        confirm.resolve(True)
        assert done.wait(WAIT)
    finally:
        confirm.bind(None, None)


def test_request_without_show_still_refuses():
    confirm._pending = None
    confirm.bind(None, None)
    ran = []
    msg = confirm.request("k", "T", "d", lambda: ran.append(1), report=True)
    assert "not available" in msg and not ran
