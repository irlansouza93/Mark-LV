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

import atexit
import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
from collections import deque
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.redact import load_config_secrets, redact

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
        if now_idx - (year * 12 + month - 1) > keep_months:
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


def quota_reset_local(now_utc: datetime, local_tz) -> datetime:
    """The next midnight Pacific, expressed in ``local_tz`` (``None`` = the system zone)."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_UTC)
    try:
        nxt = _reset_zoneinfo(now_utc)
    except Exception:
        nxt = _reset_pure(now_utc)
    return nxt.astimezone(local_tz)


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
    "failures": (5 * 1024 * 1024, 4),
    "reports": (1 * 1024 * 1024, 2),
    "jarvis": (2 * 1024 * 1024, 5),
    "usage": (0, 0),
}
_FILES = {"failures": "failures.jsonl", "reports": "reports.jsonl", "jarvis": "jarvis.log"}
_STREAMS = ("failures", "reports", "usage")

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


class _Rotating(RotatingFileHandler):
    """A RotatingFileHandler that never prints "--- Logging error ---" itself: failures
    go to our single warning. UTF-8, lazily opened (no empty files)."""

    def __init__(self, filename, max_bytes: int, backups: int):
        super().__init__(filename, maxBytes=max_bytes, backupCount=backups,
                         encoding="utf-8", delay=True)

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
def _enqueue(stream: str, record: dict, *, ts: float | None = None) -> None:
    """Hand ``record`` to the writer thread. ``stream`` is ``failures``, ``reports`` or
    ``usage`` (the monthly file is picked from the event time). Never raises, never
    blocks: shallow copy + ``SimpleQueue.put`` (~µs). Before ``setup()`` it is a silent
    no-op, so library code and tools may call it freely."""
    global _dropped
    try:
        q, t = _q, _thread
        if q is None:
            return
        if t is None or not t.is_alive():
            _warn_once("dead",
                       "[JARVIS] errlog: writer thread is not running; "
                       "failures and usage are not being recorded.",
                       "SYS: The log writer stopped — failures and usage are not being saved.")
            return
        if q.qsize() >= _MAX_QUEUE:
            with _dropped_lock:
                _dropped += 1
            return
        if not isinstance(record, dict):
            if record is None:
                return
            record = {"value": record}
        q.put(("rec", stream, dict(record), time.time() if ts is None else ts, _live_session))
    except Exception:
        pass


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
        mt = None
    if mt != _sec_mtime:
        _sec_values = load_config_secrets(_CONFIG_PATH) if mt is not None else []
        _sec_mtime = mt
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
    try:
        return _clean_str(str(obj))
    except Exception:
        return f"<{type(obj).__name__}>"


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…[+{len(text) - limit} chars]"


def _truncate(obj, key: str | None = None):
    if isinstance(obj, str):
        return _cut(obj, _MAX_TRACEBACK if key == "traceback" else _MAX_TEXT)
    if isinstance(obj, dict):
        return {k: _truncate(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate(v, key) for v in obj]
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


def _write_record(stream: str, record: dict, ts: float, session) -> None:
    if stream not in _STREAMS:
        _warn_once(f"stream:{stream}", f"[JARVIS] errlog: unknown log stream {stream!r}; record dropped.")
        return
    rec = dict(record)
    exc = rec.pop("exc", None)
    if isinstance(exc, BaseException):
        rec.setdefault("traceback", "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        rec.setdefault("error_type", type(exc).__name__)
        rec.setdefault("message", str(exc))
    when = datetime.fromtimestamp(ts).astimezone()
    out = {"v": 1, "ts": when.isoformat(timespec="milliseconds"), "app": version(),
           "pid": os.getpid(), "live_session": session}
    for k, v in rec.items():
        if k not in out:
            out[k] = v
    dropped = _take_dropped()
    if dropped:
        out["dropped"] = dropped
    # Order matters: normalize (JSON-safe) -> redact the FULL text -> only then cut, so a
    # secret that straddles the cut is masked rather than half-shown.
    out = _truncate(redact(_normalize(out), _current_secrets()))
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


def _write_text(tag: str, line: str, ts: float) -> None:
    stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    text = _cut(redact(_clean_str(line), _current_secrets()), _MAX_LINE)
    try:
        _emit(_handler("jarvis", "jarvis", _FILES["jarvis"]), f"{stamp} {tag} {text}")
    except Exception as e:
        _warn_write_failed(e)


def _writer_loop(q: queue.SimpleQueue) -> None:
    while True:
        item = q.get()
        try:
            kind = item[0]
            if kind == "rec":
                _write_record(item[1], item[2], item[3], item[4])
            elif kind == "text":
                _write_text(item[1], item[2], item[3])
            elif kind == "flush":
                item[1].set()
            elif kind == "stop":
                item[1].set()
                return
        except Exception as e:                  # one bad record must never end the thread
            try:
                _warn_write_failed(e)
            except Exception:
                pass


def flush(timeout_s: float = 1.0) -> None:
    """Wait (at most ``timeout_s``) until everything queued so far is on disk. Used by
    ``atexit`` and by tests. Never raises."""
    try:
        q, t = _q, _thread
        if q is None or t is None or not t.is_alive():
            return
        done = threading.Event()
        q.put(("flush", done))
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
    flush(1.0)


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
                lines.append(self._buf)
                self._buf = ""
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


def _excepthook(tp, val, tb) -> None:
    try:
        if not issubclass(tp, KeyboardInterrupt):          # Ctrl+C is not a failure
            _enqueue("failures", {"kind": "uncaught", "component": "app", "name": "main",
                                  "exc": val})
    except Exception:
        pass
    (_prev_excepthook or sys.__excepthook__)(tp, val, tb)


def _thread_excepthook(args) -> None:
    try:
        if args.exc_type is not SystemExit and args.exc_value is not None:
            name = getattr(args.thread, "name", None) or "unknown"
            _enqueue("failures", {"kind": "uncaught", "component": "thread", "name": name,
                                  "exc": args.exc_value})
    except Exception:
        pass
    (_prev_thread_hook or threading.__excepthook__)(args)


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
    global _dir, _q, _thread, _dropped, _live_session, _notify, _transcript, _sec_mtime
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
