"""core/errlog.py — the pure helpers (no disk, no threads, no clock of their own)."""
import calendar
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone

import pytest

from core import errlog

UTC = timezone.utc
BRT = timezone(timedelta(hours=-3))          # fixed UTC-3, like Brasilia


# ── parse_version (research R6) ──────────────────────────────────────────────
CHANGELOG = """# Changelog

## [Unreleased]

### Added
- something

## [55.1.0] — 2026-10-15

## [55.0.0] — 2026-09-27
"""
HASH = "8f9b865" + "0" * 33


def test_version_from_loose_ref():
    got = errlog.parse_version("ref: refs/heads/irlan-dev\n", HASH + "\n", None, CHANGELOG)
    assert got == "55.1.0+8f9b865"


def test_version_from_packed_refs_only():
    packed = ("# pack-refs with: peeled fully-peeled sorted\n"
              f"{'a' * 40} refs/heads/main\n"
              f"{HASH} refs/heads/irlan-dev\n")
    got = errlog.parse_version("ref: refs/heads/irlan-dev", None, packed, CHANGELOG)
    assert got == "55.1.0+8f9b865"


def test_version_from_detached_head():
    assert errlog.parse_version(HASH + "\n", None, None, CHANGELOG) == "55.1.0+8f9b865"


def test_version_without_git_uses_nogit():
    assert errlog.parse_version(None, None, None, CHANGELOG) == "55.1.0+nogit"


def test_version_with_nothing_is_unknown():
    assert errlog.parse_version(None, None, None, None) == "unknown"
    assert errlog.parse_version(None, None, None, "# no versions here\n") == "unknown"


def test_version_skips_unreleased_heading():
    only_unreleased = "## [Unreleased]\n\n- x\n"
    assert errlog.parse_version(None, None, None, only_unreleased) == "unknown"


def test_version_unresolvable_ref_falls_back_to_nogit():
    # HEAD points at a ref that exists neither loose nor packed.
    got = errlog.parse_version("ref: refs/heads/ghost", None, "", CHANGELOG)
    assert got == "55.1.0+nogit"


def test_read_version_plain_git_folder(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/x\n")
    (git / "refs" / "heads" / "x").write_text(HASH + "\n")
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert errlog.read_version(tmp_path) == "55.1.0+8f9b865"


def test_read_version_worktree_gitdir_file(tmp_path):
    common = tmp_path / "main" / ".git"
    wt_git = common / "worktrees" / "w1"
    (common / "refs" / "heads").mkdir(parents=True)
    wt_git.mkdir(parents=True)
    (wt_git / "HEAD").write_text("ref: refs/heads/feat\n")
    (wt_git / "commondir").write_text("../..\n")
    (common / "refs" / "heads" / "feat").write_text(HASH + "\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {wt_git}\n")
    (work / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    assert errlog.read_version(work) == "55.1.0+8f9b865"


def test_read_version_never_raises_on_empty_root(tmp_path):
    assert errlog.read_version(tmp_path) == "unknown"


# ── months_to_delete (research R10) ──────────────────────────────────────────
def test_months_to_delete_only_older_than_twelve_months():
    names = ["usage-2025-09.jsonl", "usage-2025-10.jsonl", "usage-2025-11.jsonl", "usage-2026-10.jsonl",
             "usage-2024-01.jsonl"]
    got = errlog.months_to_delete(names, date(2026, 10, 16))
    # Exactly 12 months stay: Nov 2025 .. Oct 2026.
    assert sorted(got) == ["usage-2024-01.jsonl", "usage-2025-09.jsonl", "usage-2025-10.jsonl"]


def test_months_to_delete_ignores_other_files():
    names = ["usage-raw.jsonl", "failures.jsonl", "jarvis.log", "usage-2020-13.jsonl",
             "usage-2020-1.jsonl", "notes.txt"]
    assert errlog.months_to_delete(names, date(2026, 10, 16)) == []


def test_months_to_delete_handles_year_boundary():
    got = errlog.months_to_delete(["usage-2025-01.jsonl", "usage-2025-02.jsonl", "usage-2024-12.jsonl"],
                                  date(2026, 1, 5))
    assert sorted(got) == ["usage-2024-12.jsonl", "usage-2025-01.jsonl"]


# ── BurstLimiter (research R10) ──────────────────────────────────────────────
def test_burst_limiter_lets_twenty_through_then_counts():
    lim = errlog.BurstLimiter()
    allowed = [lim.allow("sigA", 100.0 + i * 0.1) for i in range(25)]
    assert allowed == [True] * 20 + [False] * 5
    # Another signature is independent.
    assert lim.allow("sigB", 101.0) is True


def test_burst_limiter_drains_once_per_minute():
    lim = errlog.BurstLimiter()
    for i in range(25):
        lim.allow("sigA", 100.0 + i * 0.1)
    assert lim.drain_suppressed(110.0) == {}                 # less than a minute since the first drop
    assert lim.drain_suppressed(163.0) == {"sigA": 5}        # a minute after the first drop (t=102)
    assert lim.drain_suppressed(170.0) == {}                 # already emitted
    assert lim.drain_suppressed(300.0) == {}


def test_burst_limiter_window_slides():
    lim = errlog.BurstLimiter()
    for i in range(20):
        assert lim.allow("sigA", 100.0)
    assert lim.allow("sigA", 100.5) is False
    assert lim.allow("sigA", 161.0) is True                  # old entries aged out of the 60 s window


def test_burst_limiter_force_drain():
    lim = errlog.BurstLimiter()
    for i in range(22):
        lim.allow("s", 10.0)
    assert lim.drain_suppressed(10.5, force=True) == {"s": 2}


# ── quota_reset_local (research R11) ─────────────────────────────────────────
def test_dates_used_below_are_the_right_sundays():
    assert calendar.weekday(2026, 3, 8) == 6      # 2nd Sunday of March 2026
    assert calendar.weekday(2026, 11, 1) == 6     # 1st Sunday of November 2026


@pytest.mark.parametrize("now_utc, expected_local", [
    # winter (PST, UTC-8): next midnight Pacific = 08:00Z = 05:00 at UTC-3
    (datetime(2026, 1, 15, 12, 0, tzinfo=UTC), datetime(2026, 1, 16, 5, 0, tzinfo=BRT)),
    # summer (PDT, UTC-7): next midnight Pacific = 07:00Z = 04:00 at UTC-3
    (datetime(2026, 10, 16, 15, 40, tzinfo=UTC), datetime(2026, 10, 17, 4, 0, tzinfo=BRT)),
    # day before spring-forward: midnight of Mar 8 is still PST
    (datetime(2026, 3, 7, 20, 0, tzinfo=UTC), datetime(2026, 3, 8, 5, 0, tzinfo=BRT)),
    # spring-forward day itself: midnight of Mar 9 is PDT
    (datetime(2026, 3, 8, 20, 0, tzinfo=UTC), datetime(2026, 3, 9, 4, 0, tzinfo=BRT)),
    # day before fall-back: midnight of Nov 1 is still PDT
    (datetime(2026, 10, 31, 20, 0, tzinfo=UTC), datetime(2026, 11, 1, 4, 0, tzinfo=BRT)),
    # fall-back day itself: midnight of Nov 2 is PST
    (datetime(2026, 11, 1, 20, 0, tzinfo=UTC), datetime(2026, 11, 2, 5, 0, tzinfo=BRT)),
])
def test_quota_reset_local_around_us_dst_changes(now_utc, expected_local):
    got = errlog.quota_reset_local(now_utc, BRT)
    assert got == expected_local
    assert got.utcoffset() == timedelta(hours=-3)


def test_quota_reset_is_strictly_in_the_future():
    # Exactly at Pacific midnight (07:00Z in summer) the next reset is a full day later.
    now = datetime(2026, 10, 17, 7, 0, tzinfo=UTC)
    assert errlog.quota_reset_local(now, UTC) == datetime(2026, 10, 18, 7, 0, tzinfo=UTC)


def test_quota_reset_fallback_matches_zoneinfo_when_available():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        zoneinfo.ZoneInfo("America/Los_Angeles")
    except Exception:
        pytest.skip("no tz database on this machine (the fallback is the only path)")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for hours in range(0, 24 * 365, 7):
        now = base + timedelta(hours=hours)
        assert errlog._reset_pure(now) == errlog._reset_zoneinfo(now), now


def test_quota_reset_works_without_pacific_zoneinfo(monkeypatch):
    # Simulate the Windows .venv without tzdata: the fallback must carry on alone.
    def no_tz(now):
        raise KeyError("America/Los_Angeles")
    monkeypatch.setattr(errlog, "_reset_zoneinfo", no_tz)
    got = errlog.quota_reset_local(datetime(2026, 10, 16, 15, 40, tzinfo=UTC), BRT)
    assert got == datetime(2026, 10, 17, 4, 0, tzinfo=BRT)


def test_quota_reset_with_system_local_tz_returns_aware_datetime():
    got = errlog.quota_reset_local(datetime(2026, 10, 16, 15, 40, tzinfo=UTC), None)
    assert got.tzinfo is not None


# ── signature (research R5) ──────────────────────────────────────────────────
def test_signature_is_twelve_hex_of_sha1():
    sig = errlog.signature("plugin", "spotify", "ValueError", "plugins/spotify.py:run")
    assert re.fullmatch(r"[0-9a-f]{12}", sig)
    expected = hashlib.sha1(b"plugin:spotify|ValueError|plugins/spotify.py:run").hexdigest()[:12]
    assert sig == expected
    assert sig != errlog.signature("plugin", "spotify", "KeyError", "plugins/spotify.py:run")
    assert sig != errlog.signature("action", "spotify", "ValueError", "plugins/spotify.py:run")
    assert sig != errlog.signature("plugin", "spotify", "ValueError", "plugins/spotify.py:other")


def _raise_a():
    raise ValueError("first line")


def _tb_of(fn):
    try:
        fn()
    except Exception as e:
        return e.__traceback__


def test_signature_ignores_line_numbers():
    # Same function, two different raise lines: the origin (hence the signature) is equal.
    def go(flag):
        if flag:
            raise ValueError("here")
        raise ValueError("there")
    tbs = []
    for flag in (True, False):
        try:
            go(flag)
        except ValueError as e:
            tbs.append(e.__traceback__)
    (o1, l1), (o2, l2) = (errlog.innermost_project_frame(t, errlog.ROOT) for t in tbs)
    assert o1 == o2 and l1 != l2
    assert errlog.signature("tool", "x", "ValueError", o1) == errlog.signature("tool", "x", "ValueError", o2)


# ── innermost_project_frame ──────────────────────────────────────────────────
def test_innermost_frame_of_a_test_function():
    origin, line = errlog.innermost_project_frame(_tb_of(_raise_a), errlog.ROOT)
    assert origin == "tests/test_errlog_pure.py:_raise_a"
    assert isinstance(line, int) and line > 0


def test_innermost_frame_skips_stdlib_frames():
    def parse_bad_json():
        json.loads("{not json")                     # raises deep inside the stdlib json package
    origin, _ = errlog.innermost_project_frame(_tb_of(parse_bad_json), errlog.ROOT)
    assert origin == "tests/test_errlog_pure.py:parse_bad_json"


def test_innermost_frame_skips_site_packages_inside_the_project(tmp_path):
    lib = tmp_path / ".venv" / "Lib" / "site-packages" / "fakepkg.py"
    app = tmp_path / "app" / "mod.py"
    ns: dict = {}
    exec(compile("def lib_fail():\n    raise ValueError('inside a dependency')\n", str(lib), "exec"), ns)
    exec(compile("def run():\n    lib_fail()\n", str(app), "exec"), ns)
    origin, line = errlog.innermost_project_frame(_tb_of(ns["run"]), tmp_path)
    assert origin == "app/mod.py:run" and line == 2


def test_innermost_frame_none_when_nothing_belongs_to_the_project(tmp_path):
    assert errlog.innermost_project_frame(_tb_of(_raise_a), tmp_path) is None
    assert errlog.innermost_project_frame(None, errlog.ROOT) is None


# ── classify_soft_failure (research R1) ──────────────────────────────────────
@pytest.mark.parametrize("text, expected", [
    ("Could not save: [Errno 13] C:\\x\\y.txt", "soft:could not save"),
    ("Could not open 'report 2024.xlsx' (3 tries)", "soft:could not open"),
    ("Unable to reach the printer at 192.168.0.4", "soft:unable to reach"),
    ("Click failed: element not found", "soft:click failed"),
    ("Click error: no window", "soft:click error"),
    ("Search failed: HTTP 503", "soft:search failed"),
    ("Error opening the camera", "soft:error opening the"),
    ("Error: boom", "soft:error"),
])
def test_soft_failure_is_recognised_with_a_stable_prefix(text, expected):
    assert errlog.classify_soft_failure(text) == expected


@pytest.mark.parametrize("text", [
    "Done.", "Opened Excel", "", "Saved 3 files", "No results found for cats",
    "Tool 'open_app' failed: boom",
    "Sir, the 'spotify' plugin failed: boom",
    "Everything went fine, there was no error: just a word in the middle of a long sentence",
])
def test_normal_results_are_not_soft_failures(text):
    assert errlog.classify_soft_failure(text) is None


def test_soft_failure_prefix_ignores_numbers_paths_and_quotes():
    a = errlog.classify_soft_failure("Could not save: C:\\a\\1.txt")
    b = errlog.classify_soft_failure("Could not save: /home/u/2.txt")
    c = errlog.classify_soft_failure("could not save 'x'")
    assert a == b == c == "soft:could not save"


# ── group ────────────────────────────────────────────────────────────────────
def _rec(sig, ts, app="55.1.0+aaa", **kw):
    base = {"signature": sig, "ts": ts, "app": app, "component": "plugin", "name": "p",
            "error_type": "ValueError", "origin": "plugins/p.py:run", "message": "m"}
    base.update(kw)
    return base


def test_group_counts_and_orders():
    recs = [_rec("aaa", f"2026-10-16T10:0{i}:00-03:00") for i in range(5)]
    recs += [_rec("bbb", "2026-10-16T09:00:00-03:00", app="55.1.0+bbb", name="q"),
             _rec("bbb", "2026-10-16T11:30:00-03:00", app="55.2.0+ccc", name="q")]
    groups = errlog.group(recs)
    assert [g["signature"] for g in groups] == ["aaa", "bbb"]
    a, b = groups
    assert a["count"] == 5 and b["count"] == 2
    assert a["first_ts"] == "2026-10-16T10:00:00-03:00" and a["last_ts"] == "2026-10-16T10:04:00-03:00"
    assert b["first_ts"] == "2026-10-16T09:00:00-03:00" and b["last_ts"] == "2026-10-16T11:30:00-03:00"
    assert a["versions"] == ["55.1.0+aaa"] and b["versions"] == ["55.1.0+bbb", "55.2.0+ccc"]
    assert (a["component"], a["name"], a["error_type"], a["origin"]) == ("plugin", "p", "ValueError", "plugins/p.py:run")
    assert a["example"]["message"] == "m"


def test_group_counts_suppressed_burst_records():
    recs = [_rec("aaa", "2026-10-16T10:00:00-03:00"),
            _rec("aaa", "2026-10-16T10:01:00-03:00", suppressed=40)]
    (g,) = errlog.group(recs)
    assert g["count"] == 41                                  # 1 written + 40 held back (the summary stands for 40)


def test_group_ignores_records_without_signature():
    groups = errlog.group([{"kind": "report"}, _rec("aaa", "2026-10-16T10:00:00-03:00")])
    assert len(groups) == 1 and groups[0]["count"] == 1


# ── classify_live_error (the cues main.py's connection loop already uses) ────
@pytest.mark.parametrize("err, resumed, expected", [
    ("1011 429 RESOURCE_EXHAUSTED. quota exceeded", False, "quota"),
    ("Session resumption handle is invalid", True, "handle"),
    ("404 NOT_FOUND", True, "handle"),
    ("INVALID_ARGUMENT: bad handle", True, "handle"),
    ("404 NOT_FOUND model xyz", False, "other"),
    ("TimeoutError()", False, "net"),
    ("connection timed out", False, "net"),
    ("[Errno 11001] getaddrinfo failed", False, "net"),
    ("ConnectionRefusedError: [WinError 1225]", False, "net"),
    ("OSError: [WinError 10054] connection reset", False, "net"),
    ("Cannot connect to host generativelanguage.googleapis.com", False, "net"),
    ("API key not valid. Please pass a valid API key.", False, "key"),
    ("400 INVALID_ARGUMENT. API key not valid", False, "key"),
    ("received 1007 (invalid frame payload data)", False, "key"),
    ("INVALID_ARGUMENT: Unknown name \"proactivity\"", False, "config"),
    ("got an unexpected keyword argument 'x'", False, "config"),
    ("something nobody has seen before", False, "other"),
    ("", False, "other"),
])
def test_classify_live_error(err, resumed, expected):
    assert errlog.classify_live_error(err, resumed) == expected


def test_classify_live_error_resumed_defaults_to_false():
    assert errlog.classify_live_error("404 NOT_FOUND") == "other"
