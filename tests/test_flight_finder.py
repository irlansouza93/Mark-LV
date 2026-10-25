"""flight_finder after the 2026-10-23 triage: the right trip, one search at a time, and no
speak() that reaches the model as a user turn."""
import threading

from actions import flight_finder as ff


def test_url_carries_only_the_requested_trip():
    url = ff._build_google_flights_url("MAO", "SDQ", "2026-11-10")
    assert "tfs=" not in url                      # the fixed IST → LHR blob won over q=
    assert "MAO" in url and "SDQ" in url and "2026-11-10" in url


def test_second_call_while_one_runs_is_refused(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(*a):
        started.set()
        release.wait(5)
        return "", "url"

    monkeypatch.setattr(ff, "_search_flights_browser", slow)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault(
        "first", ff.flight_finder({"origin": "MAO", "destination": "SDQ", "date": "2026-11-10"})))
    t.start()
    assert started.wait(5)
    second = ff.flight_finder({"origin": "MAO", "destination": "SDQ", "date": "2026-11-10"})
    release.set()
    t.join(5)
    assert "already running" in second
    assert out["first"].startswith("Could not retrieve flight data")
    # the lock is free again afterwards
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a: ("", "url"))
    assert "already running" not in ff.flight_finder(
        {"origin": "MAO", "destination": "SDQ", "date": "2026-11-10"})


def test_never_speaks(monkeypatch):
    said = []
    monkeypatch.setattr(ff, "_search_flights_browser", lambda *a: ("page text", "url"))
    monkeypatch.setattr(ff, "_parse_flights_with_gemini", lambda *a: [
        {"airline": "Azul", "departure": "08:00", "arrival": "14:00", "stops": 1,
         "price": "500", "currency": "USD"}])
    out = ff.flight_finder({"origin": "MAO", "destination": "SDQ", "date": "2026-11-10"},
                           speak=said.append)
    assert said == []                              # speak() is a USER turn to the model
    assert "Azul" in out                           # the result goes back as data instead
