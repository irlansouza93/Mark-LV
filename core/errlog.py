"""
Failure log, user reports, console log and app version (spec 001).

Everything JARVIS used to lose when the window closed — the console, the tracebacks
the actions print and then swallow — lands in ``logs/`` as readable files that
``lv-triage`` and a human can open:

    jarvis.log        the console (stdout/stderr tee) — text, rotating
    failures.jsonl    one failure per line (JSON Lines), with context and signature
    reports.jsonl     "it did not work" reports the user dictated
    usage-YYYY-MM.jsonl   LLM usage, one file per month (written by core/llm_usage.py)

Design rules (constitution III, VII, VIII):
  * Public record functions never raise and only put a ``dict`` on a queue — no disk,
    no traceback formatting, no regex on the caller's thread (the audio loops and the
    Qt thread call these).
  * ONE daemon writer thread does the rest: formats tracebacks, masks secrets
    (core/redact.py), truncates free text, serializes, writes with rotation.
  * Nothing here may make the app slower or crash it. A disk that stops accepting
    writes produces exactly one warning (console + on-screen log) and the records are
    dropped; the queue is bounded so memory never grows without limit.

The pure helpers at the top (version, retention, burst limiter, quota clock) do no
I/O of their own beyond what their docstring says, so tests run them directly.
"""
from __future__ import annotations

import asyncio
import atexit
import hashlib
import io
import json
import logging
import math
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import types
import unicodedata
import uuid
from collections import deque
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.redact import read_config_secrets, redact

ROOT = Path(__file__).resolve().parent.parent
_UTC = timezone.utc


# ═════════════════════════════════════════════════════════════════════════════
#  Pure helpers
# ═════════════════════════════════════════════════════════════════════════════

# ── App version (research R6) ────────────────────────────────────────────────
_CHANGELOG_VERSION = re.compile(r"^##\s*\[(\d+(?:\.\d+)+)\]", re.M)
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


def parse_version(git_head: str | None, ref_text: str | None,
                  packed_refs: str | None, changelog: str | None) -> str:
    """``55.1.0+8f9b865`` from the text of the files git keeps (no ``git`` process).

    ``git_head`` is .git/HEAD, ``ref_text`` the loose ref it points at, ``packed_refs``
    .git/packed-refs, ``changelog`` CHANGELOG.md. No commit found -> ``55.1.0+nogit``
    (a zip install); no CHANGELOG version either -> ``unknown``.
    """
    base = None
    if changelog:
        m = _CHANGELOG_VERSION.search(changelog)        # skips "## [Unreleased]": it has no digits
        if m:
            base = m.group(1)

    commit = None
    head = (git_head or "").strip()
    if head.startswith("ref:"):
        ref = head[4:].strip()
        loose = (ref_text or "").strip().split()
        if loose and _COMMIT.match(loose[0]):
            commit = loose[0]
        else:
            for line in (packed_refs or "").splitlines():
                if line.startswith(("#", "^")):
                    continue
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _COMMIT.match(parts[0]):
                    commit = parts[0]
                    break
    elif _COMMIT.match(head):                            # detached HEAD
        commit = head

    short = commit[:7] if commit else None
    if base is None:
        return f"unknown+{short}" if short else "unknown"
    return f"{base}+{short or 'nogit'}"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def read_version(root: Path = ROOT) -> str:
    """Read the files for :func:`parse_version`. Understands a worktree's ``.git`` file
    (``gitdir: …`` + ``commondir``) because this fork is developed in worktrees.
    Never raises."""
    try:
        root = Path(root)
        dotgit = root / ".git"
        gitdir = None
        if dotgit.is_dir():
            gitdir = dotgit
        elif dotgit.is_file():
            txt = (_read_text(dotgit) or "").strip()
            if txt.startswith("gitdir:"):
                p = Path(txt[7:].strip())
                gitdir = p if p.is_absolute() else (root / p)
        head = ref = packed = None
        if gitdir is not None:
            head = _read_text(gitdir / "HEAD")
            common = gitdir
            cd = _read_text(gitdir / "commondir")
            if cd and cd.strip():
                common = (gitdir / cd.strip()).resolve()
            packed = _read_text(common / "packed-refs")
            if head and head.strip().startswith("ref:"):
                refname = head.strip()[4:].strip()
                ref = _read_text(gitdir / refname) or _read_text(common / refname)
        return parse_version(head, ref, packed, _read_text(root / "CHANGELOG.md"))
    except Exception:
        return "unknown"


# ── Retention (research R10) ─────────────────────────────────────────────────
_USAGE_FILE = re.compile(r"^usage-(\d{4})-(\d{2})\.jsonl$")


def months_to_delete(filenames, today: date, keep_months: int = 12) -> list[str]:
    """Names of ``usage-YYYY-MM.jsonl`` files older than ``keep_months`` months.
    Other files (``usage-raw.jsonl``, ``failures.jsonl``…) are never listed."""
    now_idx = today.year * 12 + (today.month - 1)
    out = []
    for name in filenames:
        m = _USAGE_FILE.match(name)
        if not m:
            continue
        year, month = int(m.group(1)), int(m.group(2))
        if not 1 <= month <= 12:
            continue
        if now_idx - (year * 12 + month - 1) >= keep_months:      # this month + the 11 before it = 12
            out.append(name)
    return out


# ── Burst limiter (research R10) ─────────────────────────────────────────────
class BurstLimiter:
    """At most ``limit`` records per signature in any ``window_s`` seconds; the rest are
    only counted. The clock is injected (``now``) so tests need no sleeping.

    A plugin failing in a loop would otherwise write thousands of identical lines a
    minute; counted this way the file stays small (spec SC-008) and nothing is lost —
    ``drain_suppressed`` hands back the tallies once a minute to be written as a single
    record with ``suppressed: N``."""

    def __init__(self, limit: int = 20, window_s: float = 60.0):
        self._limit = limit
        self._window = window_s
        self._hits: dict[str, deque] = {}
        self._suppressed: dict[str, int] = {}
        self._since: float | None = None

    def allow(self, signature: str, now: float) -> bool:
        q = self._hits.setdefault(signature, deque())
        while q and now - q[0] >= self._window:
            q.popleft()
        if len(q) < self._limit:
            q.append(now)
            return True
        self._suppressed[signature] = self._suppressed.get(signature, 0) + 1
        if self._since is None:
            self._since = now
        return False

    def drain_suppressed(self, now: float, force: bool = False) -> dict[str, int]:
        """``{signature: n}`` of what was held back, at most once per window; ``force`` for shutdown."""
        if not self._suppressed:
            return {}
        if not force and self._since is not None and now - self._since < self._window:
            return {}
        out, self._suppressed, self._since = self._suppressed, {}, None
        return out


# ── When the free quota comes back (research R11) ────────────────────────────
# Gemini's free tier resets at midnight Pacific time. Measured: the Windows .venv has
# NO tz database (ZoneInfo("America/Los_Angeles") raises, tzdata not installed), and a
# new dependency for one warning line is not worth it. So: zoneinfo when the machine
# has it (Linux/macOS), otherwise the US rule in plain code. Both paths are tested
# against each other wherever zoneinfo exists.
def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _pacific_is_dst_at_midnight(d: date) -> bool:
    """Is 00:00 Pacific on date ``d`` in daylight time? DST runs from the 2nd Sunday of
    March to the 1st Sunday of November, switching at 02:00 — so midnight of the switch
    day itself still belongs to the old regime (hence ``start < d <= end``)."""
    return _nth_sunday(d.year, 3, 2) < d <= _nth_sunday(d.year, 11, 1)


def _reset_pure(now_utc: datetime) -> datetime:
    """Next Pacific midnight after ``now_utc`` as an aware UTC datetime, by the US DST rule."""
    now = now_utc.astimezone(_UTC)
    base = now.date()
    for k in range(0, 3):
        d = base + timedelta(days=k)
        offset_h = 7 if _pacific_is_dst_at_midnight(d) else 8
        cand = datetime(d.year, d.month, d.day, tzinfo=_UTC) + timedelta(hours=offset_h)
        if cand > now:
            return cand
    raise RuntimeError("unreachable: a Pacific midnight always falls within 2 days")


def _reset_zoneinfo(now_utc: datetime) -> datetime:
    from zoneinfo import ZoneInfo                      # raises where there is no tz database
    tz = ZoneInfo("America/Los_Angeles")
    loc = now_utc.astimezone(tz)
    nxt = datetime(loc.year, loc.month, loc.day, tzinfo=tz) + timedelta(days=1)
    return nxt.astimezone(_UTC)


def pacific_day(now_utc: datetime) -> date:
    """The Pacific-time calendar date of ``now_utc`` — the "quota day" of Google's free tier,
    which rolls over at midnight there (4-5 a.m. in Brasilia). Same US DST rule as above."""
    now = now_utc.astimezone(_UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=_UTC)
    best = None
    for k in (-1, 0, 1):
        d = now.date() + timedelta(days=k)
        midnight = datetime(d.year, d.month, d.day, tzinfo=_UTC) + timedelta(
            hours=7 if _pacific_is_dst_at_midnight(d) else 8)
        if midnight <= now:
            best = d
    return best


def quota_reset_local(now_utc: datetime, local_tz) -> datetime:
    """The next midnight Pacific, expressed in ``local_tz`` (``None`` = the system zone)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_UTC)
    try:
        nxt = _reset_zoneinfo(now_utc)
    except Exception:
        nxt = _reset_pure(now_utc)
    return nxt.astimezone(local_tz)


# ── Signature, origin, grouping (research R5) ────────────────────────────────
def signature(component, name, error_type, origin) -> str:
    """12 hex chars identifying "the same failure": component, name, error type and the
    innermost project frame as ``file.py:function`` — deliberately WITHOUT a line number,
    which changes on every edit and would split one bug into many groups."""
    return hashlib.sha1(f"{component}:{name}|{error_type}|{origin}".encode("utf-8", "replace")).hexdigest()[:12]


# Directories whose files belong to someone else even when they sit under the project root
# (this repo keeps its venv in .venv/).
_FOREIGN_DIRS = {".venv", "venv", "site-packages", "dist-packages"}


def _project_rel(filename: str, root: Path) -> str | None:
    """``filename`` relative to ``root`` with forward slashes, or None when it is outside."""
    if not filename or filename.startswith("<"):
        return None
    root_s = os.path.abspath(root)
    path = os.path.abspath(filename)
    if not os.path.normcase(path).startswith(os.path.normcase(root_s) + os.sep):
        return None
    return path[len(root_s) + 1:].replace("\\", "/")


def innermost_project_frame(tb, root: Path = ROOT):
    """The deepest traceback frame that is project code, as ``("rel/path.py:function", line)``.
    Frames in the stdlib, in site-packages and in ``.venv`` are skipped, so an error raised
    deep inside a library is attributed to the line of ours that called it. None when no
    frame belongs to the project."""
    found = None
    while tb is not None:
        code = tb.tb_frame.f_code
        rel = _project_rel(code.co_filename, root)
        if rel is not None and not (_FOREIGN_DIRS & {p.lower() for p in rel.split("/")}):
            found = (f"{rel}:{code.co_name}", tb.tb_lineno)
        tb = tb.tb_next
    return found


# ── Soft failures (research R1) ──────────────────────────────────────────────
# Most actions catch their own exceptions and return a sentence ("Could not save: …").
# Those never reach a loader, so the tool dispatcher shows each result to this
# classifier. It is conservative on purpose: the result must START like an error (or
# carry the error word within its first few words), so a normal sentence that happens to
# contain the word "error" is not a failure.
#
# Calibrated on the ~250 error strings the actions in actions/ really return (T020a):
#   "Access denied: …" "Permission denied: …"            file_controller, file_processor
#   "File not found: …" "Command not found: …"            file_controller, code_helper, video
#   "Convert failed: …" "Scheduling failed: …"            "<what> failed:" in 30+ actions
#   "Click error: …" "File controller error (copy): …"    "<what> error:" (computer_control…)
#   "Flight search failed, sir: …"                        the butler register of flight/youtube/summary
#   "pandas not installed. Run: …"                        missing optional dependency
#   "Unsupported format: …" "Unknown PDF action: …"       bad request that reached the handler
#   "Timed out after 30s." "Sir, I couldn't find …"       timeouts and first-person failures
# NOT failures: "No results found for…", "No files found.", "Desktop is empty.", "Not found
# in monitored topics: …" (an empty answer is an answer), and "Please specify…"/"No file
# path provided." (the model called the tool badly — a prompt problem, left to a later pass).
_SOFT_START = re.compile(
    r"^(?:could\s+not\b|couldn't|unable\s+to\b|failed\s+to\b|cannot\b|can't|error\b"
    r"|access\s+denied|permission\s+denied|unsupported\b|unknown\s+\S|invalid\b|no\s+such\s+file"
    r"|something\s+went\s+wrong|protected\s+directory"
    r"|(?:sir,\s*)?i\s+(?:couldn't|could\s+not|can't|cannot|am\s+unable|was\s+unable)\b)", re.I)
# "<up to 3 words> failed:" / "error:" — also "error (copy):" and "failed, sir:".
_SOFT_LABELLED = re.compile(r"^(?!(?:the|a|an|this|that|these|there|it|no|in|to|of)\b)(?:\S+\s+){0,3}(?:failed|error)(?:\s*\([^)]*\))?(?:,\s*sir)?:", re.I)
# "<subject> not installed|not found|timed out|not supported" within the first words.
_SOFT_PHRASE = re.compile(
    r"^(?:\S+\s+){0,3}(?:(?:is\s+)?not\s+(?:installed|available)\b|not\s+a\s+(?:file|directory)\b"
    r"|not\s+found(?:\s*[:.(]|\s+on\s+screen)|timed\s+out\b)"
    r"|^(?:\S+\s+){0,4}not\s+supported\b", re.I)
# Results the loaders already recorded as a crash: counting them again would double them.
_SOFT_ALREADY = re.compile(r"^(?:tool '[^']*' failed\b|sir, the '[^']*' plugin failed\b)", re.I)
_QUOTED = re.compile(r"""(?<![A-Za-z])(['"]).*?\1""")
_PARENS = re.compile(r"\([^)]*\)")


def classify_soft_failure(result) -> str | None:
    """``"soft:could not save"`` when ``result`` reads like a swallowed error, else None.
    The label is the first three plain words before any colon, lower-cased, with quoted
    text, parentheses, paths and numbers dropped — so the same failure on different files
    groups together."""
    text = result.strip() if isinstance(result, str) else ""
    if not text or _SOFT_ALREADY.match(text):
        return None
    if not (_SOFT_START.match(text) or _SOFT_LABELLED.match(text) or _SOFT_PHRASE.match(text)):
        return None
    head = _PARENS.sub(" ", _QUOTED.sub(" ", text.split(":", 1)[0].lower()))
    head = " ".join(tok for tok in head.split() if "/" not in tok and "\\" not in tok)
    words = re.findall(r"[a-z']+", head)
    if words and words[0] == "sir":                      # "Sir, I couldn't…" is the same failure as "I couldn't…"
        words = words[1:]
    return "soft:" + (" ".join(words[:3]) or "unknown")


def _ts_key(ts) -> float:
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return 0.0


def group(records) -> list:
    """Failure records -> groups by signature, most frequent first. A record that carries
    ``suppressed: N`` is the burst limiter's summary and stands for N occurrences that were
    not written, so the count is the real number of times it happened."""
    groups: dict = {}
    for r in records:
        sig = r.get("signature") if isinstance(r, dict) else None
        if not sig:
            continue
        n = int(r.get("suppressed") or 1)
        g = groups.get(sig)
        if g is None:
            groups[sig] = g = {"signature": sig, "count": 0, "first_ts": r.get("ts"), "last_ts": r.get("ts"),
                               "versions": set(), "component": r.get("component"), "name": r.get("name"),
                               "error_type": r.get("error_type"), "origin": r.get("origin"), "example": r}
        g["count"] += n
        if _ts_key(r.get("ts")) < _ts_key(g["first_ts"]):
            g["first_ts"] = r.get("ts")
        if _ts_key(r.get("ts")) >= _ts_key(g["last_ts"]):
            g["last_ts"] = r.get("ts")
            g["example"] = r
        if r.get("app"):
            g["versions"].add(r["app"])
    out = []
    for g in groups.values():
        g["versions"] = sorted(g["versions"])
        out.append(g)
    out.sort(key=lambda g: (-g["count"], -_ts_key(g["last_ts"])))
    return out


# ── Exceptions: flattening groups and chains ─────────────────────────────────
def _exception_leaves(exc, out: list, depth: int = 0) -> list:
    """The non-group exceptions inside ``exc`` (a TaskGroup wraps the real cause in an
    ExceptionGroup, sometimes nested)."""
    if isinstance(exc, BaseExceptionGroup) and depth < 8:
        for sub in exc.exceptions:
            _exception_leaves(sub, out, depth + 1)
    else:
        out.append(exc)
    return out


def _primary_exception(exc):
    """The leaf worth reporting: the first that is not a CancelledError (cancellation is
    the group tearing its other tasks down, never the cause). ``exc`` itself when it is
    not a group."""
    leaves = _exception_leaves(exc, [])
    for leaf in leaves:
        if not isinstance(leaf, asyncio.CancelledError):
            return leaf
    return leaves[0] if leaves else exc


def _chain_text(exc, limit: int = 50) -> str:
    """``Type: message`` of an exception and of everything it wraps — group members,
    ``__cause__`` and ``__context__`` — for matching by cue. Cycle-safe and bounded."""
    seen: set = set()
    parts: list = []
    todo = [exc]
    while todo and len(parts) < limit:
        e = todo.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        try:
            parts.append(f"{type(e).__name__}: {e}")
        except Exception:
            parts.append(type(e).__name__)
        if isinstance(e, BaseExceptionGroup):
            todo.extend(e.exceptions)
        todo.append(e.__cause__)
        todo.append(e.__context__)
    return "\n".join(parts)


# ── Voice connection errors (the cues main.py's connection loop already uses) ─
# Plus what a real Wi-Fi drop looks like (2026-10-17): websockets' ConnectionClosedError,
# genai's "1006 … abnormal closure" and Windows' ConnectionResetError [WinError 64].
_NET_CUES = ("TimeoutError", "timed out", "getaddrinfo", "CancelledError",
             "ConnectionRefusedError", "OSError", "Cannot connect",
             "ConnectionClosed", "ConnectionReset", "ConnectionAbort", "abnormal closure",
             "WinError 64", "Network is unreachable")
# Whole words: "unhandled errors in a TaskGroup" (every TaskGroup failure) must not read as a handle.
_HANDLE_WORD = re.compile(r"\b(?:resum\w*|handle)\b")
_CONFIG_CUES = ("INVALID_ARGUMENT", "Unknown name", "unexpected keyword")


def classify_live_error(err, resumed: bool = False) -> str:
    """``quota | handle | net | key | config | other`` for a failed Live connection.

    ``err`` is the exception (preferred: its group members, ``__cause__`` and ``__context__``
    are all read — a dropped Wi-Fi arrives as an ExceptionGroup whose own text says nothing)
    or just its text. Same order of reasons as the loop in main.py. ``handle`` needs
    ``resumed``: only a session opened with a resumption handle can have a rejected handle.
    A bad key is checked before the generic INVALID_ARGUMENT because Google reports it as one."""
    if isinstance(err, BaseException):
        s = _chain_text(err)
    else:
        s = str(err or "")
    low = s.lower()
    if resumed and (_HANDLE_WORD.search(low) or "INVALID_ARGUMENT" in s or "NOT_FOUND" in s):
        return "handle"
    if "429" in s or "RESOURCE_EXHAUSTED" in s:
        return "quota"
    if "API key not valid" in s:
        return "key"
    if any(c in s for c in _CONFIG_CUES):
        return "config"
    if "1007" in s:
        return "key"
    if any(c in s for c in _NET_CUES):
        return "net"
    return "other"


_NET_TYPES = ("ConnectError", "ConnectionError", "gaierror")
_NET_TEXT = ("getaddrinfo", "nodename nor servname", "name or service not known",
             "network is unreachable", "no route to host", "cannot connect")


def rung_failure_reason(exc, msg: str = "") -> str:
    """Why a Gemini ladder rung failed when it was not quota, gone or unavailable:
    ``network`` (no route / DNS — every rung will fail the same way), ``timeout`` or ``error``."""
    text = (msg or "").lower()
    seen: set = set()
    todo = [exc]
    while todo and len(seen) < 20:
        e = todo.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, (ConnectionError, socket.gaierror)) or type(e).__name__ in _NET_TYPES:
            return "network"
        todo.extend((e.__cause__, e.__context__))
    if any(c in text for c in _NET_TEXT):
        return "network"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in text:
        return "timeout"
    return "error"


def ladder_message(reasons: dict, now: datetime | None = None) -> str:
    """The one on-screen line for an exhausted Gemini ladder (research R12)."""
    n = len(reasons)
    if n and all(v == "network" for v in reasons.values()):
        return "SYS: Gemini unreachable — check the connection."
    quota = sum(1 for v in reasons.values() if v == "quota")
    other = n - quota
    # The predominant reason (FR-004a): quota, outage, or both when neither is two thirds of it.
    if quota and quota * 3 >= n * 2:
        back = quota_reset_local(now or datetime.now(_UTC), None)
        return f"SYS: Gemini free quota used up on {quota} models — back at {back:%H:%M}."
    if quota and other * 3 < n * 2:
        back = quota_reset_local(now or datetime.now(_UTC), None)
        return (f"SYS: Gemini free quota used up on {quota} models and {other} not answering "
                f"— quota back at {back:%H:%M}.")
    return f"SYS: Gemini models not answering ({n} tried) — try again shortly."


# ═════════════════════════════════════════════════════════════════════════════
#  Queue + writer thread
# ═════════════════════════════════════════════════════════════════════════════

# Where the secrets to mask come from. A module variable so tests can point it at a
# throwaway file instead of the real config/api_keys.json.
_CONFIG_PATH = ROOT / "config" / "api_keys.json"

# (max bytes per file, number of backups) per file family — research R10. The totals
# are 25 MB failures + 3 MB reports + 12 MB console; usage files are monthly, never
# rotated by size (~150 KB/day estimated).
_LIMITS = {
    "usage_raw": (5 * 1024 * 1024, 1),     # JARVIS_USAGE_RAW=1 diagnostics only (research R7)
    "failures": (5 * 1024 * 1024, 4),
    "reports": (1 * 1024 * 1024, 2),
    "jarvis": (2 * 1024 * 1024, 5),
    "usage": (0, 0),
}
_FILES = {"failures": "failures.jsonl", "reports": "reports.jsonl", "jarvis": "jarvis.log",
          "usage_raw": "usage-raw.jsonl"}
_STREAMS = ("failures", "reports", "usage", "usage_raw")

# A stuck disk must not grow memory without bound: past this many queued items new
# records are dropped and the next one written says how many were lost.
_MAX_QUEUE = 10_000
_MAX_TEXT = 500            # free text per field
_MAX_TRACEBACK = 8_000
_MAX_LINE = 2_000          # one console line in jarvis.log (a print of a huge JSON must not fill it)
_MAX_DEPTH = 8             # nesting we are willing to serialize (also what breaks reference cycles)
_MAX_ITEMS = 100           # list/dict entries kept per container

_lock = threading.RLock()                 # setup/shutdown only; never taken by record functions
_dir: Path | None = None
_q: queue.SimpleQueue | None = None
_thread: threading.Thread | None = None
_handlers: dict = {}                      # touched by the writer thread (and shutdown after it stopped)
_dropped = 0
_dropped_lock = threading.Lock()
_live_session: str | None = None
_notify = None                            # callable(str) bound by main.py (JarvisUI.write_log)
_transcript = None                        # callable() -> list[str] bound by main.py
_version: str | None = None
_warned: set = set()                      # one warning per kind per process
_sec_mtime: object = "unset"
_sec_values: list = []
_atexit_registered = False
# Original console streams, kept so our own warnings never go through the tee.
_orig_streams: dict = {}


# After a rotation fails (Windows: another process — an editor, a viewer, antivirus — has the
# file open, WinError 32) the handler keeps appending past the size limit and tries again only
# after this many records or seconds. Without it every emit retried, failed and lost its record
# (34 of 35 records, with the rotation half done).
_ROTATE_RETRY_RECORDS = 200
_ROTATE_RETRY_S = 60.0


class _Rotating(RotatingFileHandler):
    """A RotatingFileHandler that never prints "--- Logging error ---" itself: failures
    go to our single warning. UTF-8, lazily opened (no empty files). A rotation that cannot
    rename does not cost records: it reopens the current file and carries on."""

    def __init__(self, filename, max_bytes: int, backups: int):
        super().__init__(filename, maxBytes=max_bytes, backupCount=backups,
                         encoding="utf-8", delay=True)
        self._rotate_blocked = 0                 # records written since the last failed rotation (0 = not blocked)
        self._rotate_retry_at = 0.0

    def doRollover(self):
        if self._rotate_blocked and (self._rotate_blocked < _ROTATE_RETRY_RECORDS
                                     and time.monotonic() < self._rotate_retry_at):
            self._rotate_blocked += 1            # still waiting before trying again: just keep appending
            return
        try:
            super().doRollover()
            self._rotate_blocked = 0
        except OSError as e:
            self._rotate_blocked = 1
            self._rotate_retry_at = time.monotonic() + _ROTATE_RETRY_S
            try:
                if self.stream is None:
                    self.stream = self._open()   # the file was closed for the rename: reopen it in append mode
            except OSError:
                pass
            _warn_once(f"rotate:{self.baseFilename}",
                       f"[JARVIS] errlog: could not rotate {Path(self.baseFilename).name} "
                       f"({type(e).__name__}: {e}); keeping on appending to it and retrying later.",
                       "SYS: A log file is locked by another program — logging continues without rotating it.")

    def handleError(self, record):
        _warn_write_failed(sys.exc_info()[1])


def version() -> str:
    """JARVIS version, read once (research R6)."""
    global _version
    if _version is None:
        _version = read_version(ROOT)
    return _version


def set_live_session(session_id: str | None) -> None:
    """Stamp the id of the active voice session on the records that follow."""
    global _live_session
    _live_session = session_id


_live_attempts = 0


def new_live_session() -> str:
    """A short id for the voice session that just connected; stamped on every record until
    the next one. Also zeroes the consecutive-failure counter, so ``attempt`` in a live
    failure means "how many tries since the last time it worked"."""
    global _live_attempts
    _live_attempts = 0
    sid = uuid.uuid4().hex[:8]
    set_live_session(sid)
    return sid


def next_live_attempt() -> int:
    """1, 2, 3… for consecutive failed connection attempts (reset by ``new_live_session``)."""
    global _live_attempts
    _live_attempts += 1
    return _live_attempts


def bind(*, notify=None, transcript=None) -> None:
    """Connect the on-screen log (``notify(text)``, thread-safe) and the conversation
    provider for reports (``transcript() -> list[str]``). ``None`` leaves a binding as is."""
    global _notify, _transcript
    if notify is not None:
        _notify = notify
    if transcript is not None:
        _transcript = transcript


# ── Our own warnings ─────────────────────────────────────────────────────────
def _console(msg: str) -> None:
    """Print to the console as it was before the tee (never through it: a failing log
    must not feed itself)."""
    try:
        stream = _orig_streams.get("stderr") or sys.stderr
        if stream is not None:
            stream.write(msg + "\n")
            stream.flush()
    except Exception:
        pass


def _notify_screen(msg: str) -> None:
    try:
        if _notify is not None:
            _notify(msg)
    except Exception:
        pass


def _warn_once(key: str, console_msg: str, screen_msg: str | None = None) -> None:
    if key in _warned:
        return
    _warned.add(key)
    _console(console_msg)
    if screen_msg:
        _notify_screen(screen_msg)


def _warn_write_failed(exc: BaseException | None) -> None:
    detail = f"{type(exc).__name__}: {exc}" if exc is not None else "unknown error"
    _warn_once(
        "write",
        f"[JARVIS] errlog: log writing failed ({_dir}): {detail}. "
        "Failures and usage are not being saved until this is fixed.",
        f"SYS: Could not write logs ({type(exc).__name__ if exc else 'error'}) "
        "— failures and usage are not being saved.",
    )


# ── Caller side: put a dict on the queue and go ──────────────────────────────
def _enqueue(stream: str, record: dict, *, ts: float | None = None) -> str | None:
    """Hand ``record`` to the writer thread. ``stream`` is ``failures``, ``reports``,
    ``usage`` (the monthly file is picked from the event time) or ``usage_raw``. Never
    raises, never blocks: shallow copy + ``SimpleQueue.put`` (~µs). Before ``setup()`` it is
    a silent no-op, so library code and tools may call it freely.

    Returns None when the record was queued, else a short reason ("logging is not running",
    "the log writer is not running", "the log queue is full") — which almost every caller
    ignores and ``record_report`` hands to the user."""
    global _dropped
    try:
        q, t = _q, _thread
        if q is None:
            return "logging is not running"
        if t is None or not t.is_alive():
            _warn_once("dead",
                       "[JARVIS] errlog: writer thread is not running; "
                       "failures and usage are not being recorded.",
                       "SYS: The log writer stopped — failures and usage are not being saved.")
            return "the log writer is not running"
        if q.qsize() >= _MAX_QUEUE:
            with _dropped_lock:
                _dropped += 1
            return "the log queue is full"
        if not isinstance(record, dict):
            if record is None:
                return None
            record = {"value": record}
        q.put(("rec", stream, dict(record), time.time() if ts is None else ts, _live_session))
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _enqueue_text(tag: str, line: str) -> None:
    """Console line for jarvis.log (the tee uses this)."""
    global _dropped
    try:
        q, t = _q, _thread
        if q is None or t is None or not t.is_alive():
            return
        if q.qsize() >= _MAX_QUEUE:
            with _dropped_lock:
                _dropped += 1
            return
        q.put(("text", tag, line, time.time()))
    except Exception:
        pass


def _take_dropped() -> int:
    global _dropped
    with _dropped_lock:
        n, _dropped = _dropped, 0
    return n


# ── Writer side ──────────────────────────────────────────────────────────────
def _current_secrets() -> list:
    """Secrets of config/api_keys.json, re-read only when the file's mtime changes."""
    global _sec_mtime, _sec_values
    try:
        mt = _CONFIG_PATH.stat().st_mtime_ns
    except OSError:
        return _sec_values                       # file gone for a moment (being replaced): keep what we know
    if mt != _sec_mtime:
        got = read_config_secrets(_CONFIG_PATH)
        if got is None:
            return _sec_values                   # unreadable now (mid-rewrite): keep the old ones and retry next time
        _sec_values, _sec_mtime = got, mt        # only a successful read moves the mtime forward
    return _sec_values


def _clean_str(s: str) -> str:
    # A lone surrogate (from a mangled console string) would make the UTF-8 write fail
    # and cost the whole record.
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "replace").decode("utf-8")


def _normalize(obj, depth: int = 0):
    """Anything -> JSON-safe primitives. Unknown objects become their ``str``; containers
    deeper than ``_MAX_DEPTH`` (also circular ones) and longer than ``_MAX_ITEMS`` are cut."""
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, str):
        return _clean_str(obj)
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj).decode("utf-8", "replace")
    if depth >= _MAX_DEPTH:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= _MAX_ITEMS:
                out["…"] = f"+{len(obj) - _MAX_ITEMS} items"
                break
            out[k if isinstance(k, str) else _clean_str(str(k))] = _normalize(v, depth + 1)
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = list(obj)
        out = [_normalize(v, depth + 1) for v in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            out.append(f"…[+{len(items) - _MAX_ITEMS} items]")
        return out
    dump = getattr(obj, "model_dump", None)           # google-genai (pydantic) objects, e.g. usage_metadata
    if callable(dump):
        try:
            return _normalize(dump(mode="json", exclude_none=True), depth + 1)
        except Exception:
            pass
    if isinstance(obj, types.SimpleNamespace):
        return _normalize(vars(obj), depth + 1)
    try:
        return _clean_str(str(obj))
    except Exception:
        return f"<{type(obj).__name__}>"


# Cutting text BEFORE the secret filter can leave a piece of a key: a 47-character key that
# crosses the cut keeps 35 readable characters on one side (reproduced in review). So every
# cut before the filter leaves SLACK extra characters — longer than any key — and the real,
# final cut is made by the writer AFTER the filter. A key crossing the early cut then lies
# entirely beyond the final limit and is gone with it. The early cut also bounds what the
# filter has to scan: a 2 MB console line held the GIL for ~33 ms (35 ms per MB of text).
_SLACK = 512
_CALLER_CUT = _MAX_TEXT + _SLACK               # what a caller-side clip keeps (note_tool)
_CUT_MARK = re.compile(r"…\[\+(\d+) chars\]$")


def _cut(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters plus a ``…[+N chars]`` counter. A text already cut
    earlier (it ends with such a counter) keeps counting the characters that were really lost."""
    if len(text) <= limit:
        return text
    already = 0
    m = _CUT_MARK.search(text, max(0, len(text) - 32))
    if m:
        already = int(m.group(1))
        text = text[:m.start()]
        if len(text) <= limit:
            return f"{text}…[+{already} chars]"
    return f"{text[:limit]}…[+{len(text) - limit + already} chars]"


def _truncate(obj, key: str | None = None, slack: int = 0):
    if isinstance(obj, str):
        return _cut(obj, (_MAX_TRACEBACK if key == "traceback" else _MAX_TEXT) + slack)
    if isinstance(obj, dict):
        return {k: _truncate(v, k, slack) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(v, key, slack) for v in obj]
    return obj


def _handler(key: str, family: str, filename: str) -> "_Rotating":
    h = _handlers.get(key)
    if h is None:
        _dir.mkdir(parents=True, exist_ok=True)            # may raise: the caller reports it once
        max_bytes, backups = _LIMITS[family]
        h = _Rotating(_dir / filename, max_bytes, backups)
        _handlers[key] = h
    return h


def _emit(h: "_Rotating", line: str) -> None:
    h.emit(logging.LogRecord("errlog", logging.INFO, "", 0, line, None, None))


_FAILURE_ORDER = ("kind", "component", "name", "phase", "error_type", "message", "traceback",
                  "origin", "origin_line", "signature", "context", "suppressed")

# Burst limiter state (writer thread only): research R10 / spec SC-008.
_burst = BurstLimiter()
# The failures just written (writer thread only), for the "recent failures" of a user report.
_recent_failures: deque = deque(maxlen=50)
_held: dict = {}            # signature -> (prepared record, live session) of what was held back


def _apply_exc(rec: dict, exc) -> None:
    """Fill traceback / error_type / message from an exception object — on the writer
    thread, because formatting a traceback costs 0.1-1 ms and the caller may be the
    audio loop. The traceback is always the full one (for a group: every member); type
    and message come from the primary leaf, so two different causes wrapped in the same
    ``ExceptionGroup`` do not collapse into one signature."""
    try:
        rec.setdefault("traceback", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        rec.setdefault("traceback", f"{type(exc).__name__} (traceback unavailable)")
    leaf = _primary_exception(exc)
    rec.setdefault("error_type", type(leaf).__name__)
    if not rec.get("message"):
        try:
            rec["message"] = str(leaf)
        except Exception:
            rec["message"] = f"<{type(leaf).__name__}: str() failed>"


def _prepare_failure(rec: dict, exc) -> None:
    """Origin and signature of a failure (research R5). The origin is the innermost
    project frame of the exception (of its primary leaf when it is a group); failing that,
    the line of ours that reported it (``_caller``, captured by ``record_failure``);
    failing that, ``component:name``."""
    caller = rec.pop("_caller", None)
    frame = None
    if isinstance(exc, BaseException):
        leaf = _primary_exception(exc)
        frame = (innermost_project_frame(leaf.__traceback__, ROOT)
                 or innermost_project_frame(exc.__traceback__, ROOT))
    if not rec.get("origin"):
        if frame is not None:
            rec["origin"], rec["origin_line"] = frame
        elif caller:
            rel = _project_rel(caller[0], ROOT) or os.path.basename(caller[0])
            rec["origin"], rec["origin_line"] = f"{rel}:{caller[1]}", caller[2]
        else:
            rec["origin"] = f"{rec.get('component')}:{rec.get('name')}"
    rec.setdefault("origin_line", None)
    rec.setdefault("traceback", None)
    rec.setdefault("error_type", rec.get("kind") or "unknown")
    rec.setdefault("signature", signature(rec.get("component"), rec.get("name"),
                                          rec["error_type"], rec["origin"]))


def _write_record(stream: str, record: dict, ts: float, session, _summary: bool = False) -> None:
    if stream not in _STREAMS:
        _warn_once(f"stream:{stream}", f"[JARVIS] errlog: unknown log stream {stream!r}; record dropped.")
        return
    rec = dict(record)
    # A record may carry a builder (``_finalize(rec, ts) -> dict | None``) so that the work of
    # shaping it — token extraction, price lookup, session bookkeeping — runs HERE, on the
    # writer thread, and not on the caller's (core/llm_usage.py uses this for every record).
    # Returning None means "nothing to write".
    fin = rec.pop("_finalize", None)
    if fin is not None:
        try:
            rec = fin(rec, ts)
        except Exception as e:
            _warn_once("finalize", f"[JARVIS] errlog: building a record failed: {type(e).__name__}: {e}")
            return
        if not rec:
            return
    exc = rec.pop("exc", None)
    if isinstance(exc, BaseException):
        _apply_exc(rec, exc)
    if stream == "failures":
        rec.pop("transcript", None)                        # FR-017: only a user's report carries the conversation
        _prepare_failure(rec, exc)
        if not _summary and not _burst.allow(rec["signature"], ts):
            _held[rec["signature"]] = (rec, session)       # tallied; written as one summary per minute
            return
        if not _summary:
            _recent_failures.append((ts, {"ts": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"),
                                          "signature": rec["signature"], "component": rec.get("component"),
                                          "name": rec.get("name"), "error_type": rec.get("error_type")}))
    when = datetime.fromtimestamp(ts).astimezone()
    out = {"v": 1, "ts": when.isoformat(timespec="milliseconds"), "app": version(),
           "pid": os.getpid(), "live_session": session}
    if stream == "failures":
        for k in _FAILURE_ORDER:
            if k in rec:
                out[k] = rec[k]
    for k, v in rec.items():
        if k not in out:
            out[k] = v
    dropped = _take_dropped()
    if dropped:
        out["dropped"] = dropped
    # Order matters: normalize (JSON-safe) -> early cut WITH slack (bounds the filter's work) ->
    # redact -> the real cut. A secret straddling the real cut is masked first; one straddling
    # the early cut lies past the real limit and is removed by it (see _SLACK).
    out = _truncate(redact(_truncate(_normalize(out), slack=_SLACK), _current_secrets()))
    line = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    try:
        if stream == "usage":
            name = f"usage-{when:%Y-%m}.jsonl"
            h = _handler(name, "usage", name)
        else:
            h = _handler(stream, stream, _FILES[stream])
        _emit(h, line)
    except Exception as e:
        _warn_write_failed(e)


_clock = time.time           # a seam: tests move the minute without sleeping


def _drain_suppressed(force: bool = False) -> None:
    """Write the "N more of these" summaries the burst limiter is holding (at most one
    per signature per minute, or all of them at exit)."""
    now = _clock()
    for sig, n in _burst.drain_suppressed(now, force).items():
        held = _held.pop(sig, None)
        if held is None:
            continue
        rec, session = held
        _write_record("failures", {**rec, "suppressed": n}, now, session, _summary=True)


def _write_text(tag: str, line: str, ts: float) -> None:
    stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    text = _cut(redact(_clean_str(_cut(line, _MAX_LINE + _SLACK)), _current_secrets()), _MAX_LINE)
    try:
        _emit(_handler("jarvis", "jarvis", _FILES["jarvis"]), f"{stamp} {tag} {text}")
    except Exception as e:
        _warn_write_failed(e)


def _writer_loop(q: queue.SimpleQueue) -> None:
    while True:
        try:
            item = q.get(timeout=5.0)          # wakes up idle so a held burst summary is not stuck
        except queue.Empty:
            item = ("tick",)
        try:
            kind = item[0]
            if kind == "rec":
                _write_record(item[1], item[2], item[3], item[4])
            elif kind == "text":
                _write_text(item[1], item[2], item[3])
            elif kind == "flush":
                if item[2]:
                    _drain_suppressed(force=True)
                item[1].set()
            elif kind == "stop":
                _drain_suppressed(force=True)
                item[1].set()
                return
            _drain_suppressed()
        except Exception as e:                  # one bad record must never end the thread
            try:
                _warn_write_failed(e)
            except Exception:
                pass
            if item[0] in ("flush", "stop"):
                item[1].set()
                if item[0] == "stop":
                    return


def flush(timeout_s: float = 1.0, *, final: bool = False) -> None:
    """Wait (at most ``timeout_s``) until everything queued so far is on disk. ``final``
    (atexit) also writes the burst summaries still being held. Never raises."""
    try:
        q, t = _q, _thread
        if q is None or t is None or not t.is_alive():
            return
        done = threading.Event()
        q.put(("flush", done, final))
        done.wait(timeout_s)
    except Exception:
        pass


def _clean_old_usage(log_dir: Path) -> None:
    try:
        names = os.listdir(log_dir)
    except OSError:
        return
    for name in months_to_delete(names, date.today()):
        try:
            os.remove(log_dir / name)
        except OSError:
            pass


def _stop_writer(timeout_s: float = 2.0) -> None:
    q, t = _q, _thread
    if q is not None and t is not None and t.is_alive():
        done = threading.Event()
        q.put(("stop", done))
        done.wait(timeout_s)
        t.join(timeout_s)


def _stop_writer_for_test() -> None:
    """Kill the thread but keep the module "set up", to exercise the dead-writer path."""
    _stop_writer()


def _at_exit() -> None:
    flush(1.0, final=True)


# ═════════════════════════════════════════════════════════════════════════════
#  Recording failures (US1)
# ═════════════════════════════════════════════════════════════════════════════
def _kind_for(component, error_type) -> str:
    if component == "gemini":
        return "gemini"
    if component == "live":
        return "live"
    if isinstance(error_type, str) and error_type.startswith("soft:"):
        return "soft"
    return "exception"


def _enqueue_failure(kind, component, name, exc, error_type, message, phase, context, origin, caller) -> str | None:
    """The one door every failure goes through — hooks, loaders, excepthooks — so each gets
    the same signature, origin and burst limit. Only builds a dict; the writer does the rest."""
    rec = {"kind": kind, "component": component, "name": name, "phase": phase,
           "message": message or "", "context": dict(context) if isinstance(context, dict) else context}
    if error_type:
        rec["error_type"] = error_type
    if origin:
        rec["origin"] = origin
    if exc is not None:
        rec["exc"] = exc
    if caller:
        rec["_caller"] = caller
    return _enqueue("failures", rec)


def record_failure(component: str, name: str, *, exc: BaseException | None = None,
                   error_type: str | None = None, message: str = "", phase: str | None = None,
                   context: dict | None = None, origin: str | None = None) -> None:
    """Record one failure in logs/failures.jsonl. Never raises; costs a dict and a queue put.

    ``exc`` gives the traceback and the origin (innermost project frame). Without it the
    origin is the caller of this function — which is stable per call site, so "the same
    failure" still groups."""
    try:
        f = sys._getframe(1)
        _enqueue_failure(_kind_for(component, error_type), component, name, exc, error_type,
                         message, phase, context, origin,
                         (f.f_code.co_filename, f.f_code.co_name, f.f_lineno))
    except Exception:
        pass


# Last tools the assistant ran, for the "it did not work" report (US3) and for spotting
# soft failures. 8 entries, args/results clipped: it is memory, not a log.
_tools: deque = deque(maxlen=8)


def _recent_tools() -> list:
    return list(_tools)


_NO_SOFT_FAILURE = frozenset({"report_problem"})


def note_tool(name, args, result) -> None:
    """Called by the tool dispatcher with every result. Remembers it in the ring and, when
    the result reads like an error the action swallowed (research R1), records a ``soft``
    failure. Runs on the caller's thread: a clipped copy and one anchored regex on the
    first 300 characters, nothing else."""
    try:
        text = result if isinstance(result, str) else str(result)
        # report_problem answers "Could not log it: …" when it fails: an error report about the
        # error report would be noise, so that tool is never judged as a soft failure.
        soft = None if str(name) in _NO_SOFT_FAILURE else classify_soft_failure(text[:300])
        clipped = ({k: (_cut(v, _CALLER_CUT) if isinstance(v, str) else v) for k, v in args.items()}
                   if isinstance(args, dict) else args)
        _tools.append({"ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                       "name": str(name), "args": clipped, "result": _cut(text, _CALLER_CUT),
                       "soft_failure": soft is not None})
        if soft:
            _enqueue_failure("soft", "tool", name, None, soft, _cut(text, _CALLER_CUT), None,
                             {"args": clipped, "result": text}, "main.py:_execute_tool", None)
    except Exception:
        pass


# ── User reports: "that did not work" (US3) ──────────────────────────────────
_REPORT_WINDOW_S = 600          # failures of the last 10 minutes go with the report
_REPORT_MAX_FAILURES = 20
_REPORT_TRANSCRIPT_LINES = 6    # the last ~3 exchanges


def _fin_report(rec: dict, ts: float) -> dict:
    """Writer thread: attach the recent failures. It runs after every record queued before
    the report, so a failure that happened a moment ago is already in the ring."""
    now = _clock()
    recent = [info for t, info in sorted(_recent_failures, key=lambda e: e[0])
              if now - _REPORT_WINDOW_S <= t <= now + 5][-_REPORT_MAX_FAILURES:]
    rec["recent_failures"] = recent
    return rec


# The sentence being spoken RIGHT NOW. main.py only appends a turn to its session log at
# turn_complete, but a report is made by a tool call in the middle of the turn — so the very
# words that triggered it (measured in the first voice test of T039: both reports lacked the
# user's last sentence) were not yet anywhere. llm_usage.live_message() sees every Live message
# anyway and feeds the transcription chunks here; string appends only, bounded, no I/O.
_TURN_CAP = 4000                                   # characters kept per side of the turn in progress
_turn_user: list = []
_turn_assistant: list = []
_turn_chars = {"u": 0, "a": 0}


def _push_chunk(buf: list, side: str, text) -> None:
    if not isinstance(text, str):
        return
    t = text.strip()
    if not t:
        return
    buf.append(t)
    _turn_chars[side] += len(t) + 1
    while _turn_chars[side] > _TURN_CAP and len(buf) > 1:          # runaway speech: forget the oldest chunks
        _turn_chars[side] -= len(buf.pop(0)) + 1


# A REQUEST is what the user asked, from their first words until their NEXT words: a request that
# needs a tool passes through several turn_completes (main.py documents it), so "this turn" is
# the wrong unit for "was it already reported?". The request state below therefore survives
# end_turn() and is reset only by new speech from the user (or a new voice session).
_tool_reported = False        # the report_problem tool already wrote a report for this request
_auto_ts = None               # "ts" the automatic report of this request was written with (for follows_auto)
_last_request = ""            # the user's words of this request, kept after the turn buffers are emptied
_request_closed = False       # an end_turn() happened since the user last spoke


def _reset_request() -> None:
    global _tool_reported, _auto_ts, _last_request, _request_closed
    _tool_reported, _auto_ts, _last_request, _request_closed = False, None, "", False


def note_speech(user=None, assistant=None) -> None:
    """Add transcription chunks of the turn in progress (``input_transcription.text`` /
    ``output_transcription.text``). Called from the audio receive loop: only appends. Words from
    the user after a completed turn start a new request. Never raises."""
    try:
        if user:
            if _request_closed and isinstance(user, str) and user.strip():
                _reset_request()
            _push_chunk(_turn_user, "u", user)
        if assistant:
            _push_chunk(_turn_assistant, "a", assistant)
    except Exception:
        pass


def _clear_buffers() -> None:
    _turn_user.clear()
    _turn_assistant.clear()
    _turn_chars["u"] = _turn_chars["a"] = 0


def discard_turn() -> None:
    """Forget the turn and the request in progress without judging them (a new voice session,
    shutdown)."""
    try:
        _clear_buffers()
        _reset_request()
    except Exception:
        pass


# The automatic report (spec FR-019b, decided 2026-10-18). Two voice rounds showed the model
# writes the "it did not work" report only some of the time: when the sentence also says what
# the user wanted ("não funcionou, eu queria o Excel") it goes and does that instead (2 of 4
# noted in round 2). So the CODE listens: when a turn ends and what the user said contains one
# of these phrases, the report is written without the model. False positives are accepted; triage
# discards them. Matched on whole words after lower-casing and stripping accents, so "Não
# funcionou", "NAO FUNCIONOU" and "nao funcionou" are the same.
_PROBLEM_RX = re.compile(
    r"\b(?:nao\s+funcionou|nao\s+deu\s+certo|deu\s+errado|didn'?t\s+work|did\s+not\s+work)\b")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("‘", "'"))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def problem_trigger(text) -> bool:
    """Does what the user said report that something did not work? Pure; never raises."""
    try:
        return isinstance(text, str) and bool(_PROBLEM_RX.search(_fold(text[:_TURN_CAP])))
    except Exception:
        return False


def end_turn() -> None:
    """The turn is complete (main.py is about to log it): if the user's words in it reported a
    problem and this request has no report yet (neither the tool's nor an earlier automatic one),
    write the automatic report now; then empty the turn buffers. The request state stays until the
    user speaks again. Runs on the audio thread: one regex over a short string and one queue put."""
    global _auto_ts, _last_request, _request_closed
    try:
        said = " ".join(list(_turn_user)).strip()
        if said:
            _last_request = said
            if not _tool_reported and _auto_ts is None and problem_trigger(said):
                ts = time.time()
                if _queue_report(said, None, "auto", ts=ts) is None:
                    _auto_ts = datetime.fromtimestamp(ts).astimezone().isoformat(timespec="milliseconds")
                    notify_once("auto_report", "SYS: Problem noted for triage.", every_s=20)
        _clear_buffers()
        _request_closed = True
    except Exception:
        pass


def _turn_in_progress(provider_lines=()) -> list:
    """The ``(now)`` lines: what is being said, or — after the turn buffers were emptied but before the
    user speaks again — the user's words of this request, unless the session log already has them."""
    lines = []
    said = " ".join(list(_turn_user)).strip()
    drafted = " ".join(list(_turn_assistant)).strip()
    if not said and _last_request:
        key = _fold(_last_request)[:40]
        if key and not any(key in _fold(line) for line in provider_lines):
            said = _last_request
    if said:
        lines.append(f"User (now): {said}")
    if drafted:
        lines.append(f"Assistant (now): {drafted}")
    return lines


def record_report(expected, happened=None) -> str | None:
    """Queue a report made by the ``report_problem`` tool (``source: "tool"``) for
    logs/reports.jsonl. Returns None when queued, or a short reason (never raises). It also
    tells ``end_turn`` that this turn already has its report, so the automatic one is not
    written twice."""
    global _tool_reported
    reason = _queue_report(expected, happened, "tool")
    if reason is None:
        _tool_reported = True
    return reason


def _queue_report(expected, happened, source: str, ts: float | None = None) -> str | None:
    """The report with the last lines of the conversation (the provider bound by
    ``bind(transcript=…)`` plus the turn in progress), the last 8 tools run and the failures
    of the last 10 minutes. Only reports ever store the conversation.

    Instant: copies a few small lists and puts one dict on the queue; the failures, the
    secret filter and the disk are the writer's."""
    try:
        lines: list = []
        provider = _transcript
        if provider is not None:
            try:
                got = provider()
                lines = [str(x) for x in list(got)[-_REPORT_TRANSCRIPT_LINES:]] if got else []
            except Exception:
                lines = []
        lines = (lines + _turn_in_progress(lines))[-_REPORT_TRANSCRIPT_LINES:]    # + the sentence still being spoken
        said = str(expected).strip() if expected is not None else ""
        went_wrong = str(happened).strip() if happened is not None else ""
        rec = {"kind": "report", "source": source, "expected": said or "(not said)",
               "happened": went_wrong or None, "transcript": lines,
               "recent_tools": _recent_tools(), "_finalize": _fin_report}
        if source == "tool" and _auto_ts:
            rec["follows_auto"] = _auto_ts        # the same request already has an automatic report: triage joins them
        return _enqueue("reports", rec, ts=ts)
    except Exception as e:
        return f"{type(e).__name__}: {e}"


_notified: dict = {}
_notified_lock = threading.Lock()


def notify_once(key: str, message: str, every_s: float = 300) -> None:
    """Say ``message`` on the on-screen log at most once per ``every_s`` seconds per ``key``.
    Without a screen bound (tests, tools) it prints. Never raises."""
    try:
        now = time.monotonic()
        with _notified_lock:
            last = _notified.get(key)
            if last is not None and now - last < every_s:
                return
            _notified[key] = now
        if _notify is not None:
            _notify(message)
        else:
            print(message)
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
#  Console capture and uncaught exceptions (research R3)
# ═════════════════════════════════════════════════════════════════════════════
# The upstream has hundreds of print() calls and traceback.print_exc() that are the only
# trace of most problems. Replacing sys.stdout/sys.stderr with a tee gets all of them
# into logs/jarvis.log without editing one of them (spec FR-001).
class _Tee:
    """Writes to the original stream exactly as before and mirrors complete lines to the
    log queue. ``write`` never raises: a Windows console on a legacy code page used to
    throw UnicodeEncodeError into the caller (main.py works around that with
    ``reconfigure(errors="replace")``); here the log copy is kept regardless and the
    console gets an unencodable-safe retry."""

    def __init__(self, orig, tag: str):
        self._orig = orig
        self._tag = tag
        self._buf = ""
        self._buf_lock = threading.Lock()

    def write(self, s):
        try:
            if not isinstance(s, str):
                s = bytes(s).decode("utf-8", "replace") if isinstance(s, (bytes, bytearray)) else str(s)
            orig = self._orig
            if orig is not None:
                try:
                    orig.write(s)
                except Exception:
                    try:
                        enc = getattr(orig, "encoding", None) or "utf-8"
                        orig.write(s.encode(enc, "replace").decode(enc, "replace"))
                    except Exception:
                        pass
            self._feed(s)
            return len(s)
        except Exception:
            return 0

    def _feed(self, s: str) -> None:
        with self._buf_lock:
            self._buf += s
            *lines, self._buf = self._buf.split("\n")
            if len(self._buf) > 4 * _MAX_LINE:             # a progress bar with no newline
                # Split at the last space, not at the limit: a key has no spaces, so it cannot be
                # cut in two (each half would escape the filter). No space at all = one huge token.
                cut = self._buf.rfind(" ")
                if cut <= 0:
                    cut = len(self._buf)
                lines.append(self._buf[:cut])
                self._buf = self._buf[cut:].lstrip(" ")
        for line in lines:
            line = line.rstrip("\r")
            if line.strip():
                _enqueue_text(self._tag, line)

    def flush(self):
        try:
            if self._orig is not None:
                self._orig.flush()
        except Exception:
            pass

    # With no console (pythonw: sys.stdout is None) these answer as a closed stream would, instead
    # of raising AttributeError into code that probes them (logging, click, colorama, uvicorn).
    def isatty(self):
        try:
            return bool(self._orig.isatty()) if self._orig is not None else False
        except Exception:
            return False

    def fileno(self):
        if self._orig is None:
            raise io.UnsupportedOperation("fileno")
        return self._orig.fileno()

    @property
    def encoding(self):
        return getattr(self._orig, "encoding", None) or "utf-8"

    def __getattr__(self, name):                          # encoding, isatty, fileno, buffer, reconfigure…
        orig = self.__dict__.get("_orig")
        if orig is None:
            raise AttributeError(name)
        return getattr(orig, name)


_tees: dict = {}


def _install_tee() -> None:
    for name, tag in (("stdout", "OUT"), ("stderr", "ERR")):
        cur = getattr(sys, name, None)
        if isinstance(cur, _Tee):
            continue
        _orig_streams[name] = cur
        tee = _Tee(cur, tag)
        _tees[name] = tee
        setattr(sys, name, tee)


def _uninstall_tee() -> None:
    for name, tee in list(_tees.items()):
        if getattr(sys, name, None) is tee:                # someone replaced it since: leave theirs
            setattr(sys, name, tee._orig)
    _tees.clear()
    _orig_streams.clear()


_prev_excepthook = None
_prev_thread_hook = None


def _tell_user_of_uncaught(queued_reason) -> None:
    """The failure is logged; say so on screen (once a minute) — otherwise an exception that escapes
    is invisible unless someone reads the console."""
    if queued_reason is None:
        notify_once("uncaught", "SYS: Internal error — logged for triage.", every_s=60)


def _excepthook(tp, val, tb) -> None:
    try:
        if not issubclass(tp, KeyboardInterrupt):          # Ctrl+C is not a failure
            _tell_user_of_uncaught(_enqueue_failure("uncaught", "app", "main", val, None, "", None, None, None, None))
    except Exception:
        pass
    (_prev_excepthook or sys.__excepthook__)(tp, val, tb)


def _thread_excepthook(args) -> None:
    try:
        if args.exc_type is not SystemExit and args.exc_value is not None:
            name = getattr(args.thread, "name", None) or "unknown"
            _tell_user_of_uncaught(
                _enqueue_failure("uncaught", "thread", name, args.exc_value, None, "", None, None, None, None))
    except Exception:
        pass
    (_prev_thread_hook or threading.__excepthook__)(args)


def asyncio_exception_handler(loop, context) -> None:
    """Loop exception handler: records what asyncio would only print to the console — a
    ``QueueFull`` in a callback during a reconnection, a task destroyed while pending —
    as ``kind: "uncaught"`` / ``component: "app"``, then lets asyncio's default handler
    print it as before. Never raises."""
    try:
        ctx = context if isinstance(context, dict) else {}
        exc = ctx.get("exception")
        message = str(ctx.get("message") or "")
        is_exc = isinstance(exc, BaseException)
        _tell_user_of_uncaught(_enqueue_failure(
            "uncaught", "app", "asyncio", exc if is_exc else None,
            None if is_exc else "asyncio", message, None, {"message": message}, None, None))
    except Exception:
        pass
    try:
        loop.default_exception_handler(context)
    except Exception:
        pass


def _install_hooks() -> None:
    global _prev_excepthook, _prev_thread_hook
    if sys.excepthook is not _excepthook:
        _prev_excepthook = sys.excepthook
        sys.excepthook = _excepthook
    if threading.excepthook is not _thread_excepthook:
        _prev_thread_hook = threading.excepthook
        threading.excepthook = _thread_excepthook


def _uninstall_hooks() -> None:
    global _prev_excepthook, _prev_thread_hook
    if sys.excepthook is _excepthook:
        sys.excepthook = _prev_excepthook or sys.__excepthook__
    if threading.excepthook is _thread_excepthook:
        threading.excepthook = _prev_thread_hook or threading.__excepthook__
    _prev_excepthook = _prev_thread_hook = None


def setup(log_dir: Path = ROOT / "logs", *, tee_console: bool = True) -> None:
    """Start logging. Idempotent: the same call twice changes nothing; a different
    ``log_dir`` moves the writer there. Cleans ``usage-*.jsonl`` older than 12 months and
    reads the version once. Never raises."""
    global _dir, _q, _thread, _atexit_registered
    try:
        with _lock:
            target = Path(log_dir)
            alive = _thread is not None and _thread.is_alive()
            if not (alive and _dir == target):
                if _thread is not None or _q is not None:
                    _shutdown_locked()
                _dir = target
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except Exception:
                    pass                                    # reported once by the writer, on the first record
                _clean_old_usage(target)
                version()
                _q = queue.SimpleQueue()
                _thread = threading.Thread(target=_writer_loop, args=(_q,),
                                           name="errlog-writer", daemon=True)
                _thread.start()
            _install_hooks()
            if tee_console:
                _install_tee()
            else:
                _uninstall_tee()
            if not _atexit_registered:
                atexit.register(_at_exit)
                _atexit_registered = True
    except Exception as e:
        _console(f"[JARVIS] errlog: setup failed: {type(e).__name__}: {e}")


def _shutdown_locked() -> None:
    global _dir, _q, _thread, _dropped, _live_session, _notify, _transcript, _sec_mtime, _live_attempts
    _uninstall_tee()
    _uninstall_hooks()
    _stop_writer()
    for h in list(_handlers.values()):
        try:
            h.close()
        except Exception:
            pass
    _handlers.clear()
    _warned.clear()
    _held.clear()
    _recent_failures.clear()
    _burst.__init__()
    _tools.clear()
    discard_turn()
    _notified.clear()
    _live_attempts = 0
    _dir = _q = _thread = None
    _live_session = _notify = _transcript = None
    _sec_mtime = "unset"
    with _dropped_lock:
        _dropped = 0


def shutdown() -> None:
    """Flush, stop the writer and undo what ``setup`` installed. The app never calls this
    (the daemon thread and ``atexit`` are enough); tests do, to stay isolated."""
    try:
        with _lock:
            _shutdown_locked()
    except Exception:
        pass
