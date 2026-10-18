"""core/errlog.py — the pure helpers (no disk, no threads, no clock of their own)."""
import calendar
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
    names = ["usage-2025-09.jsonl", "usage-2025-10.jsonl", "usage-2026-10.jsonl",
             "usage-2024-01.jsonl"]
    got = errlog.months_to_delete(names, date(2026, 10, 16))
    assert sorted(got) == ["usage-2024-01.jsonl", "usage-2025-09.jsonl"]


def test_months_to_delete_ignores_other_files():
    names = ["usage-raw.jsonl", "failures.jsonl", "jarvis.log", "usage-2020-13.jsonl",
             "usage-2020-1.jsonl", "notes.txt"]
    assert errlog.months_to_delete(names, date(2026, 10, 16)) == []


def test_months_to_delete_handles_year_boundary():
    got = errlog.months_to_delete(["usage-2025-01.jsonl", "usage-2024-12.jsonl"],
                                  date(2026, 1, 5))
    assert got == ["usage-2024-12.jsonl"]


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
