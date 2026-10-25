"""core/fs_policy.py — the one place that decides which paths the assistant may touch.

WHY THIS EXISTS
    Before this module every action had its own idea of "safe": file_controller only
    accepted Path.home(), code_helper read and wrote anywhere the model named, the
    other actions did not look at all (spec 003, section Context). A model-chosen path
    is untrusted input, so the rule has to live in one pure function that every action
    calls, and the user's answer ("which folders may you open, and may you change
    them?") has to be a list they can see and edit in SETUP -> ALLOWED FOLDERS.

HOW AN ACTION USES IT
        path = fs_policy.require(raw, "write", actor="file_controller")   # raises PolicyDenied
        ...
    and the action answers with ``str(exc)`` — a sentence the model relays to the user.
    The action must use the path *returned* by require(), not the raw one: that is the
    realpath, so a symlink or junction that was checked is the thing that is opened.

WHAT IT DECIDES (contracts/fs_policy.md, "decision order")
    1. normalise (realpath, so links and junctions are followed); failure -> invalid
    2. the deny list (keys, vaults, .git, .env, the app's own secrets) -> denied; beats
       everything, including the uploaded file
    3. a read of the file the user just attached in the UI -> uploaded
    4. the most specific allowed folder that contains it; none -> outside
    5. write/exec in a read-only folder -> read_only
    6. ok

Pure: stdlib only, no Qt, no network. Config, home and app dir are read lazily and every
source can be swapped by configure() so tests never touch the real config/.
"""
from __future__ import annotations

import fnmatch
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

Op = Literal["read", "write", "exec"]

_MARK = "[FOLDER_POLICY]"
_VERB = {"read": "read", "write": "change", "exec": "run"}


# ── types ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AllowedFolder:
    path: Path
    mode: str                       # "ro" | "rw"
    label: str | None = None


@dataclass(frozen=True)
class DenyRule:
    kind: str                       # "path" | "parts" | "name"
    value: object                   # normalised path | tuple of components | name pattern
    reason: str                     # short, shown to the user


@dataclass(frozen=True)
class Decision:
    allowed: bool
    path: Path
    op: str
    reason: str                     # ok | uploaded | outside | read_only | denied | invalid
    root: Path | None = None
    deny_reason: str | None = None
    suggest: Path | None = None
    actor: str = ""

    def __str__(self) -> str:
        return "" if self.allowed else _sentence(self)


class PolicyDenied(Exception):
    """Raised by require()/confined(); str(exc) is the sentence for the model."""

    def __init__(self, decision: Decision):
        super().__init__(str(decision))
        self.decision = decision


# ── overridable sources (tests) ──────────────────────────────────────────────

_UNSET = object()
_lock = threading.RLock()
_state: dict = {"folders": None, "home": None, "app_dir": None,
                "env": None, "platform": None, "uploaded": None}
_cache: dict = {"folders": None, "rules": None}


def configure(*, folders=_UNSET, home=_UNSET, app_dir=_UNSET, env=_UNSET,
              platform=_UNSET, uploaded=_UNSET) -> None:
    """Swap the sources of the decision (tests only, FR-020).

    ``folders``: list of (path, mode) / dicts, or a callable returning one; None = read the
    real list from config_manager. Anything not passed stays as it was."""
    with _lock:
        for k, v in (("folders", folders), ("home", home), ("app_dir", app_dir),
                     ("env", env), ("platform", platform), ("uploaded", uploaded)):
            if v is not _UNSET:
                _state[k] = v
        _cache["folders"] = None
        _cache["rules"] = None


def reset() -> None:
    """Back to the real sources (tests clean up with this)."""
    with _lock:
        for k in _state:
            _state[k] = None
        _cache["folders"] = None
        _cache["rules"] = None


def reload() -> None:
    """Re-read the folder list on the next decision (FR-018: no restart)."""
    with _lock:
        _cache["folders"] = None


def bind_uploaded(fn: Callable[[], str | None] | None) -> None:
    """The UI's currently attached file: a read of exactly that file is allowed anywhere
    (the user handed it over), unless it is on the deny list (research R5)."""
    with _lock:
        _state["uploaded"] = fn


# ── normalisation ────────────────────────────────────────────────────────────

def _norm(p) -> str:
    """realpath(expanduser(p)): follows symlinks AND junctions and returns the long name
    (resolves PROGRA~1), non-strict so a file that does not exist yet still resolves
    through its nearest existing parent. Raises ValueError for anything unusable."""
    s = os.fspath(p) if isinstance(p, os.PathLike) else str(p)
    if not s.strip() or "\0" in s:
        raise ValueError("empty or NUL path")
    if os.name == "nt" and ":" in os.path.splitdrive(s)[1]:
        # NTFS alternate data stream ("proj\.env:x"): the name no longer matches the deny
        # list but the write lands in the protected file (review of spec 003).
        raise ValueError("':' after the drive (alternate data stream)")
    try:
        return os.path.realpath(os.path.expanduser(s))
    except (OSError, ValueError) as e:
        raise ValueError(str(e)) from e


def _pkey(norm: str) -> tuple:
    """Components for comparing containment: normcase (case-blind on Windows, identity
    elsewhere). Containment is by whole components, never startswith: C:\\DEV must not
    cover C:\\DEVELOPMENT."""
    return Path(os.path.normcase(norm)).parts


def _inside(parent: tuple, child: tuple) -> bool:
    return len(child) >= len(parent) and child[:len(parent)] == parent


def _home() -> str:
    h = _state["home"]
    return _norm(h if h is not None else Path.home())


def _app_dir() -> str:
    a = _state["app_dir"]
    return _norm(a if a is not None else Path(__file__).resolve().parent.parent)


# ── deny list ────────────────────────────────────────────────────────────────

_ENV_SAFE = {".env.example", ".env.sample", ".env.template"}


class _Rules:
    """The deny list compiled once: tuples of casefolded components, plus a set of the
    last component of every path/parts rule so a scan can skip the full comparison for
    nearly every item (research R7)."""

    def __init__(self, rules: list[DenyRule]):
        self.rules = rules
        self.path: list[tuple] = []
        self.seq: list[tuple] = []
        self.exact: dict[str, str] = {}
        self.suffix: list[tuple] = []
        self.prefix: list[tuple] = []
        self.last: set[str] = set()
        for r in rules:
            if r.kind == "path":
                v = tuple(x.casefold() for x in Path(str(r.value)).parts)
                self.path.append((v, r.reason))
                self.last.add(v[-1])
            elif r.kind == "parts":
                v = tuple(x.casefold() for x in r.value)
                self.seq.append((v, r.reason))
                self.last.add(v[-1])
            else:
                pat = str(r.value).casefold()
                if pat.startswith("*"):
                    self.suffix.append((pat[1:], r.reason))
                elif pat.endswith("*"):
                    self.prefix.append((pat[:-1], r.reason))
                else:
                    self.exact[pat] = r.reason
        self._suffixes = tuple(s for s, _ in self.suffix)
        self._prefixes = tuple(s for s, _ in self.prefix)
        # One regex per directory instead of one test per file (see walk()): it matches every
        # name that could be hidden - a superset of by_name() + the `last` names - joined by
        # NUL, which no file name can contain.
        starts = [re.escape(x) for x in self._prefixes] + [r"\.env\."]
        exact = [re.escape(n) for n in sorted(self.exact) + sorted(self.last)]
        # every suffix starts with "." -> one literal dot to look for, then the extensions: the
        # engine only branches at dots instead of trying each suffix at every character.
        ends = [r"\.(?:" + "|".join(re.escape(x[1:]) for x in self._suffixes) + ")"]
        self._gate = re.compile(
            r"(?:^|\0)(?:" + "|".join(starts) + "|(?:" + "|".join(exact) + r")(?=\0|$))"
            + "|(?:" + "|".join(ends) + r")(?=\0|$)")      # names are casefolded before the search

    def by_name(self, name: str) -> str | None:
        n = name.casefold()
        if n in _ENV_SAFE:
            return None
        if n in self.exact:
            return self.exact[n]
        if n.endswith(self._suffixes):
            for s, r in self.suffix:
                if n.endswith(s):
                    return r
        if n.startswith(self._prefixes):
            for s, r in self.prefix:
                if n.startswith(s):
                    return r
        return None

    def by_parts(self, parts: tuple) -> str | None:
        for v, r in self.path:
            if parts[:len(v)] == v:
                return r
        for v, r in self.seq:
            n = len(v)
            for i in range(len(parts) - n + 1):
                if parts[i:i + n] == v:
                    return r
        return None


def _env() -> dict:
    e = _state["env"]
    return os.environ if e is None else e


def _build_rules() -> _Rules:
    home = Path(_home())
    app = Path(_app_dir())
    plat = _state["platform"] or sys.platform
    env = _env()
    out: list[DenyRule] = []

    def path(p, reason):
        out.append(DenyRule("path", _norm(p), reason))

    def parts(seq, reason):
        out.append(DenyRule("parts", tuple(seq), reason))

    def name(pat, reason):
        out.append(DenyRule("name", pat, reason))

    # The app's own secrets, by exact place AND by shape: worktrees and other clones of the
    # Mark LV carry a copy of config/api_keys.json outside app_dir (CLAUDE.md, "a worktree
    # has no api_keys.json: copy it"), and a folder like C:\DESENVOLVIMENTO that contains
    # them is exactly what the user will allow (research R3).
    path(app / "config" / "api_keys.json", "app secrets")
    path(app / "config" / "certs", "app secrets")
    path(app / "config" / "whatsapp_web", "app secrets")
    path(app / "memory" / "long_term.json", "app secrets")
    parts(("config", "api_keys.json"), "app secrets")
    parts(("config", "certs"), "app secrets")
    parts(("config", "whatsapp_web"), "app secrets")
    parts(("memory", "long_term.json"), "app secrets")
    parts((".git",), "git internals")

    for d, why in ((".ssh", "SSH keys"), (".gnupg", "GPG keys"), (".aws", "cloud credentials"),
                   (".azure", "cloud credentials"), (".kube", "cluster credentials")):
        path(home / d, why)
    path(home / ".docker" / "config.json", "registry credentials")
    for f in (".git-credentials", ".netrc", ".pypirc", ".npmrc"):
        path(home / f, "stored credentials")

    if plat == "win32":
        appdata, local = env.get("APPDATA"), env.get("LOCALAPPDATA")
        if appdata:
            a = Path(appdata)
            path(a / "Microsoft" / "Credentials", "Windows credentials")
            path(a / "Microsoft" / "Protect", "Windows credentials")
            path(a / "Mozilla" / "Firefox", "browser profile")
            path(a / "Bitwarden", "password vault")
            path(a / "KeePass", "password vault")
        if local:
            l = Path(local)
            path(l / "Google" / "Chrome" / "User Data", "browser profile")
            path(l / "Microsoft" / "Edge" / "User Data", "browser profile")
            path(l / "BraveSoftware" / "Brave-Browser" / "User Data", "browser profile")
            path(l / "1Password", "password vault")
    elif plat == "darwin":
        path(home / "Library" / "Keychains", "keychain")
        for b in ("Google/Chrome", "Firefox", "BraveSoftware", "Microsoft Edge"):
            path(home / "Library" / "Application Support" / Path(b), "browser profile")
    else:
        path(home / ".local" / "share" / "keyrings", "keyring")
        path(home / ".password-store", "password vault")
        path(home / ".mozilla", "browser profile")
        for b in ("google-chrome", "chromium", "BraveSoftware"):
            path(home / ".config" / b, "browser profile")

    for pat in ("*.kdbx", "*.kdb"):
        name(pat, "password vault")
    for pat in ("*.1pux", "*.opvault"):
        name(pat, "password vault")
    for pat in ("*.pem", "*.key", "*.p12", "*.pfx"):
        name(pat, "private key")
    for pat in ("id_rsa*", "id_ed25519*", "id_ecdsa*"):
        name(pat, "SSH keys")
    name(".env", "environment secrets")
    name(".env.*", "environment secrets")     # .env.example/.sample/.template stay readable
    return _Rules(out)


def _rules() -> _Rules:
    with _lock:
        r = _cache["rules"]
        if r is None:
            r = _cache["rules"] = _build_rules()
        return r


def _deny_norm(norm: str) -> str | None:
    c = _rules()
    parts = tuple(x.casefold() for x in Path(norm).parts)
    return c.by_parts(parts) or (c.by_name(parts[-1]) if parts else None)


def deny_rules() -> list[DenyRule]:
    """The fixed deny list, for SETUP to show (read-only: FR-008)."""
    return list(_rules().rules)


def is_denied(path) -> str | None:
    """Reason from the deny list, or None. Text only (abspath, no realpath): meant for
    item-by-item scans where a handle per item would double a search (research R7). A
    link to a secret is caught when it is opened, by check()."""
    try:
        s = os.path.abspath(os.path.expanduser(os.fspath(path) if isinstance(path, os.PathLike) else str(path)))
    except (ValueError, OSError, TypeError):
        return "invalid path"
    return _deny_norm(s)


def validate_folder(path) -> str | None:
    """Why this folder cannot be allowed, or None."""
    try:
        norm = _norm(path)
    except ValueError:
        return "not a valid path"
    why = _deny_norm(norm)
    if why:
        return f"protected location ({why}) - it can never be allowed"
    return None


# ── allowed folders ──────────────────────────────────────────────────────────

def _load_folders() -> list[tuple[tuple, AllowedFolder]]:
    src = _state["folders"]
    if src is None:
        from memory import config_manager      # late: config_manager imports us back
        items = config_manager.get_allowed_folders()
    else:
        items = src() if callable(src) else list(src)
    out = []
    for it in items:
        if isinstance(it, AllowedFolder):
            p, mode, label = it.path, it.mode, it.label
        elif isinstance(it, dict):
            p, mode, label = it.get("path"), it.get("mode"), it.get("label")
        else:
            p, mode = it[0], it[1]
            label = it[2] if len(it) > 2 else None
        try:
            norm = _norm(p)
        except ValueError:
            continue
        out.append((_pkey(norm), AllowedFolder(Path(norm), mode if mode in ("ro", "rw") else "ro", label)))
    return out


def _folders() -> list[tuple[tuple, AllowedFolder]]:
    with _lock:
        f = _cache["folders"]
        if f is None:
            try:
                f = _load_folders()
            except Exception as e:
                # Unreadable list -> allow nothing rather than guess (never "all open").
                _log_failure("fs_policy", f"allowed folders unreadable: {e}")
                f = []
            _cache["folders"] = f
        return f


def allowed_folders() -> list[AllowedFolder]:
    return [af for _k, af in _folders()]


# ── decision ─────────────────────────────────────────────────────────────────

def _log_failure(actor: str, message: str, reason: str = "error", context: dict | None = None) -> None:
    try:
        from core import errlog
        errlog.record_failure(component="fs_policy", name=actor, error_type=f"policy:{reason}",
                              message=message, context=context or {})
    except Exception:
        pass        # the policy keeps working without the log


def _safe_path(p) -> Path:
    try:
        return Path(str(p))
    except Exception:
        return Path("?")


def _suggest(norm: str) -> Path | None:
    """The disk-root or user-folder child that holds the target, i.e. the smallest folder
    that is easy to name and that is not a protected location."""
    try:
        t = Path(norm)
        home = Path(_home())
        anchor = home if _inside(_pkey(str(home)), _pkey(norm)) else Path(t.anchor)
        rel = t.parts[len(anchor.parts):]
        cand = anchor / rel[0] if len(rel) >= 2 else anchor
        while not cand.exists() and cand != anchor:
            cand = cand.parent
        return None if _deny_norm(str(cand)) else cand
    except Exception:
        return None


def _decide(path, op: str, actor: str) -> Decision:
    try:
        norm = _norm(path)
    except ValueError:
        return Decision(False, _safe_path(path), op, "invalid", actor=actor)
    p = Path(norm)

    why = _deny_norm(norm)
    if why:
        return Decision(False, p, op, "denied", deny_reason=why, actor=actor)

    key = _pkey(norm)
    if op == "read":
        fn = _state["uploaded"]
        if fn is not None:
            try:
                up = fn()
                if up and _pkey(_norm(up)) == key:
                    return Decision(True, p, op, "uploaded", actor=actor)
            except Exception as e:
                # A broken UI callable must not open anything: no uploaded file, and the
                # failure is on record instead of silent.
                _log_failure(actor, "uploaded-file callable failed", "uploaded_callable",
                             {"error": type(e).__name__})

    best = None
    for k, af in _folders():
        if _inside(k, key) and (best is None or len(k) > len(best[0])):
            best = (k, af)
    if best is None:
        return Decision(False, p, op, "outside", suggest=_suggest(norm), actor=actor)
    root = best[1]
    if op in ("write", "exec") and root.mode == "ro":
        return Decision(False, p, op, "read_only", root=root.path, suggest=root.path, actor=actor)
    return Decision(True, p, op, "ok", root=root.path, actor=actor)


def check(path, op: Op, *, actor: str) -> Decision:
    """Decide; never raises. A refusal is logged (kind "policy"), a permission is not."""
    d = _decide(path, op, actor)
    if not d.allowed:
        ctx = {"op": op, "reason": d.reason}
        if d.root is not None:
            ctx["root"] = str(d.root)
        if d.deny_reason:
            ctx["deny_reason"] = d.deny_reason           # the reason only, never contents
        _log_failure(actor, f"{op} {d.path}", d.reason, ctx)
    return d


def require(path, op: Op, *, actor: str) -> Path:
    """check(); allowed -> the normalised path (use THIS one), refused -> PolicyDenied."""
    d = check(path, op, actor=actor)
    if not d.allowed:
        raise PolicyDenied(d)
    return d.path


def _sentence(d: Decision) -> str:
    where = "ALLOWED FOLDERS"
    verb = _VERB.get(d.op, d.op)
    if d.reason == "outside" and d.root is not None:           # confined(): left its folder
        why = f"it is outside {d.root}, where this has to stay"
        todo = "that this item was skipped because its path leaves the project folder"
    elif d.reason == "outside":
        why = "it is outside the folders they allowed"
        todo = (f"that they can allow {d.suggest} in \u2699 SETUP \u2192 {where}" if d.suggest
                else f"that they can allow the folder it is in via \u2699 SETUP \u2192 {where}")
    elif d.reason == "read_only":
        why = f"{d.root} is allowed as read-only"
        todo = f"that they can switch it to read and write in \u2699 SETUP \u2192 {where}"
    elif d.reason == "denied":
        why = f"it is a protected location ({d.deny_reason}) that can never be allowed"
        todo = "that this location is always off limits"
    else:
        why = "the path is not valid"
        todo = "to say the path again"
    return (f'{_MARK} I did not {verb} "{d.path}": {why}. Tell the user in their own '
            f"language, in one short sentence, {todo}. Do not try another path to get around it.")


# ── scans and containment ────────────────────────────────────────────────────

def file_url_to_path(url: str) -> str | None:
    """Local path of a ``file:`` URL, or None when ``url`` is not one. ``file:///C:/x`` is
    drive C: on Windows; ``file://host/share/x`` becomes the UNC path. Browsers and players
    read these as files, so the callers run the result through require() (review 003)."""
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname
    u = (url or "").strip()
    if not u.lower().startswith("file:"):
        return None
    parts = urlparse(u)
    path = url2pathname(parts.path)
    if parts.netloc and parts.netloc.lower() != "localhost":
        return "\\\\" + unquote(parts.netloc) + (path if path.startswith(("\\", "/")) else "\\" + path)
    return path


def is_link(p) -> bool:
    """Symlink or (Windows) junction: copytree/extractors must not follow either."""
    p = os.fspath(p)
    return os.path.islink(p) or (hasattr(os.path, "isjunction") and os.path.isjunction(p))


_is_link = is_link


def walk(root, *, actor: str) -> Iterator[tuple[Path, list[str], list[str]]]:
    """os.walk over a root the caller already require()d, minus the deny list.

    Pruned/omitted by comparing the text of each name, never with a realpath per item:
    a handle per file roughly doubles a search (research R7). Names are tested a directory
    at a time with one regex over the joined listing, and only a directory where it hits
    pays the per-name check. Measured with tools/fs_policy_bench.py (10 000 files in 100
    folders, warm cache, median of 5 `find` runs on the RTX notebook): a test per file was
    +100 % over a bare os.walk (20 ms vs 10 ms); the regex gate is 14.3 ms vs 11.3 ms,
    +3.0 ms or about 0.3 us/file. Against a loop that does nothing else that is +27 %,
    but within the revised SC-005 (no more than 5 ms extra on 10 000 files, 2026-10-23).
    Folder links and junctions are listed by name but never entered (FR-004); opening one
    goes through require()."""
    try:
        base = _norm(root)
    except ValueError:
        return
    c = _rules()
    last = c.last

    def hidden(full: str, name: str) -> bool:
        if c.by_name(name):
            return True
        if name.casefold() in last:
            return bool(c.by_parts(tuple(x.casefold() for x in Path(full).parts)))
        return False

    gate = c._gate.search

    def clean(dp: str, names: list[str]) -> list[str]:
        # Common case: nothing in this directory can be hidden -> one regex call, no
        # per-file work (a per-file test doubled a 10 000-file search; tools/fs_policy_bench.py).
        if not names or not gate("\0".join(names).casefold()):
            return names
        return [n for n in names if not hidden(os.path.join(dp, n), n)]

    for dp, dirs, files in os.walk(base, followlinks=False):
        keep = clean(dp, dirs)
        shown = clean(dp, files)
        out_dirs = list(keep)
        keep[:] = [d for d in keep if not _is_link(os.path.join(dp, d))]
        dirs[:] = keep
        yield Path(dp), out_dirs, shown


def confined(base, rel: str) -> Path:
    """base/rel, normalised, or PolicyDenied if it leaves base (FR-012): absolute paths,
    '..', another drive, or a link that points out."""
    try:
        b = _norm(base)
    except ValueError:
        raise PolicyDenied(Decision(False, _safe_path(base), "write", "invalid", actor="confined"))
    r = str(rel)

    def refuse(target: Path) -> PolicyDenied:
        d = Decision(False, target, "write", "outside", root=Path(b), actor="confined")
        _log_failure("confined", f"write {target}", "outside", {"op": "write", "reason": "outside", "root": b})
        return PolicyDenied(d)

    if "\0" in r or os.path.splitdrive(r)[0] or r[:1] in ("/", "\\"):
        raise refuse(_safe_path(r))
    try:
        t = _norm(os.path.join(b, r))
    except ValueError:
        raise refuse(_safe_path(r))
    if not _inside(_pkey(b), _pkey(t)):
        raise refuse(Path(t))
    return Path(t)
