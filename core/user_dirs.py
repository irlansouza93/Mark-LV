r"""Where the user's Desktop / Documents / Downloads / ... really are.

`Path.home() / "Desktop"` is wrong whenever the folder is redirected. Measured
on Irlan's notebook (2026-10-23): OneDrive moves the Desktop to
`C:\Users\irlan\OneDrive\Área de Trabalho` (shell API and the registry value
`User Shell Folders\Desktop` agree), while `C:\Users\irlan\Desktop` is a stale
folder with 4 leftover files. "List my desktop" showed those 4 files and the
real project folder `pdf-dark` was "not found". Documents, Pictures, etc. can
be redirected the same way, and Linux localizes them (`~/Escritorio`).

Resolution order, first existing directory wins:
  Windows: SHGetKnownFolderPath -> registry User Shell Folders -> home/<name>
  Linux:   xdg-user-dir -> ~/.config/user-dirs.dirs -> XDG_*_DIR env -> home/<name>
  macOS:   home/<name> (localization there is display-only)

Results are cached per name: the location does not change during a run, and
the ctypes/registry/subprocess lookup is not free. Tests replace the
`_known_folder`, `_registry_value` and `_xdg` functions and call `reset()`.
Pure stdlib, no Qt.
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

# name -> (English folder under home, FOLDERID GUID, User Shell Folders value,
#          xdg-user-dir name)
_INFO = {
    "desktop":   ("Desktop",   "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop", "DESKTOP"),
    "documents": ("Documents", "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Personal", "DOCUMENTS"),
    "downloads": ("Downloads", "374DE290-123F-4565-9164-39C4925E467B",
                  "{374DE290-123F-4565-9164-39C4925E467B}", "DOWNLOAD"),
    "pictures":  ("Pictures",  "33E28130-4E1E-4676-835A-98395C3BC3BB", "My Pictures", "PICTURES"),
    "music":     ("Music",     "4BD8D571-6D19-48D3-BE97-422220080E43", "My Music", "MUSIC"),
    "videos":    ("Videos",    "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "My Video", "VIDEOS"),
}

_cache: dict[str, Path] = {}


def reset() -> None:
    """Forget cached folders (tests)."""
    _cache.clear()


def _known_folder(name: str) -> str | None:
    """Windows shell answer; follows OneDrive/known-folder redirection."""
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        g = _INFO[name][1].split("-")
        tail = g[3] + g[4]
        fid = _GUID(int(g[0], 16), int(g[1], 16), int(g[2], 16),
                    (ctypes.c_ubyte * 8)(*bytes.fromhex(tail)))
        buf = ctypes.c_wchar_p()
        shell = ctypes.windll.shell32
        shell.SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p)]
        if shell.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(buf)) != 0:
            return None
        value = buf.value
        ctypes.windll.ole32.CoTaskMemFree(buf)
        return value
    except Exception:
        return None


def _registry_value(name: str) -> str | None:
    """Raw `User Shell Folders` value (may hold %VARS%)."""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return winreg.QueryValueEx(key, _INFO[name][2])[0]
    except Exception:
        return None


def _xdg(name: str) -> str | None:
    """Linux: `xdg-user-dir`, then ~/.config/user-dirs.dirs. Home itself means 'unset'."""
    key = _INFO[name][3]
    home = Path.home()
    try:
        out = subprocess.run(["xdg-user-dir", key], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out and Path(out) != home:
            return out
    except Exception:
        pass
    try:
        cfg = home / ".config" / "user-dirs.dirs"
        for line in cfg.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"XDG_{key}_DIR"):
                val = line.split("=", 1)[1].strip().strip('"').replace("$HOME", str(home))
                if Path(val) != home:
                    return val
    except Exception:
        pass
    return None


def _is_dir(value: str | None) -> Path | None:
    if not value:
        return None
    try:
        p = Path(value)
        return p if p.is_dir() else None
    except Exception:
        return None


def _resolve(name: str) -> Path:
    system = platform.system()
    if system == "Windows":
        p = _is_dir(_known_folder(name))
        if p:
            return p
        raw = _registry_value(name)
        p = _is_dir(os.path.expandvars(raw)) if raw else None
        if p:
            return p
    elif system == "Linux":
        p = _is_dir(_xdg(name))
        if p:
            return p
        p = _is_dir(os.environ.get(f"XDG_{_INFO[name][3]}_DIR", ""))
        if p:
            return p
    return Path.home() / _INFO[name][0]


def _get(name: str) -> Path:
    if name not in _cache:
        _cache[name] = _resolve(name)
    return _cache[name]


def desktop() -> Path:   return _get("desktop")
def documents() -> Path: return _get("documents")
def downloads() -> Path: return _get("downloads")
def pictures() -> Path:  return _get("pictures")
def music() -> Path:     return _get("music")
def videos() -> Path:    return _get("videos")
