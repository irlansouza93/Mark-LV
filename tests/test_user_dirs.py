"""core/user_dirs: real system folders (OneDrive redirect, XDG), no real registry."""
from pathlib import Path

import pytest

from core import user_dirs


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    user_dirs.reset()
    monkeypatch.setattr(user_dirs.Path, "home", classmethod(lambda cls: _HOME[0]))
    yield
    user_dirs.reset()


_HOME = [Path("/nonexistent-home")]


def _use_home(tmp_path, monkeypatch, system):
    _HOME[0] = tmp_path / "home"
    (tmp_path / "home").mkdir()
    monkeypatch.setattr(user_dirs.platform, "system", lambda: system)
    return tmp_path / "home"


def test_windows_known_folder_wins(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Windows")
    one = tmp_path / "OneDrive" / "Area de Trabalho"
    one.mkdir(parents=True)
    (home / "Desktop").mkdir()  # stale folder must lose
    monkeypatch.setattr(user_dirs, "_known_folder", lambda name: str(one))
    monkeypatch.setattr(user_dirs, "_registry_value", lambda name: None)
    assert user_dirs.desktop() == one


def test_windows_registry_expands_vars(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Windows")
    (home / "OneDrive" / "Docs").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(user_dirs, "_known_folder", lambda name: None)
    monkeypatch.setattr(user_dirs, "_registry_value",
                        lambda name: r"%USERPROFILE%\OneDrive\Docs")
    assert user_dirs.documents() == home / "OneDrive" / "Docs"


def test_windows_fallback_when_nothing_is_a_directory(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Windows")
    monkeypatch.setattr(user_dirs, "_known_folder", lambda name: str(tmp_path / "nope"))
    monkeypatch.setattr(user_dirs, "_registry_value", lambda name: None)
    assert user_dirs.desktop() == home / "Desktop"
    assert user_dirs.downloads() == home / "Downloads"
    assert user_dirs.videos() == home / "Videos"


def test_linux_xdg(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Linux")
    area = home / "Escritorio"
    area.mkdir()
    monkeypatch.setattr(user_dirs, "_xdg", lambda name: str(area))
    assert user_dirs.desktop() == area


def test_linux_env_then_home(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Linux")
    monkeypatch.setattr(user_dirs, "_xdg", lambda name: None)
    env = home / "env-docs"
    env.mkdir()
    monkeypatch.setenv("XDG_DOCUMENTS_DIR", str(env))
    assert user_dirs.documents() == env
    assert user_dirs.pictures() == home / "Pictures"


def test_macos_is_home_name(tmp_path, monkeypatch):
    home = _use_home(tmp_path, monkeypatch, "Darwin")
    assert user_dirs.music() == home / "Music"


def test_cache_and_reset(tmp_path, monkeypatch):
    _use_home(tmp_path, monkeypatch, "Windows")
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    calls = []
    monkeypatch.setattr(user_dirs, "_registry_value", lambda name: None)
    monkeypatch.setattr(user_dirs, "_known_folder",
                        lambda name: calls.append(name) or str(a))
    assert user_dirs.desktop() == a
    monkeypatch.setattr(user_dirs, "_known_folder", lambda name: str(b))
    assert user_dirs.desktop() == a  # cached
    user_dirs.reset()
    assert user_dirs.desktop() == b
    assert calls == ["desktop"]


def test_file_controller_desktop_shortcut_follows_user_dirs(tmp_path, monkeypatch):
    from actions import file_controller as fc
    monkeypatch.setattr(user_dirs, "desktop", lambda: tmp_path / "OneDrive")
    assert fc._resolve_path("desktop/x") == tmp_path / "OneDrive" / "x"
