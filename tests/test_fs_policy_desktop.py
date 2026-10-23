"""desktop obeys core/fs_policy (spec 003, T021): wallpaper read, organize/clean write, task confirm."""
import sys

import pytest

from actions import desktop
from core import confirm, fs_policy

MARK = "[FOLDER_POLICY]"


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = {k: tmp_path / k for k in ("home", "app", "outside", "desk")}
    for p in d.values():
        p.mkdir()
    fs_policy.configure(folders=[], home=d["home"], app_dir=d["app"], env={},
                        platform=sys.platform, uploaded=None)
    monkeypatch.setattr(desktop, "_get_desktop", lambda: d["desk"])
    yield d
    fs_policy.reset()


def test_wallpaper_outside_is_refused_before_anything_is_set(env, monkeypatch):
    img = env["outside"] / "a.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(desktop, "set_wallpaper", lambda p: pytest.fail("must not run"))
    out = desktop.desktop_control({"action": "wallpaper", "path": str(img)})
    assert out.startswith(MARK)


def test_wallpaper_in_allowed_folder_goes_through(env, monkeypatch):
    fs_policy.configure(folders=[(env["outside"], "ro")])
    img = env["outside"] / "a.png"
    img.write_bytes(b"x")
    monkeypatch.setattr(desktop, "set_wallpaper", lambda p: f"set {p}")
    assert desktop.desktop_control({"action": "wallpaper", "path": str(img)}).startswith("set ")


@pytest.mark.parametrize("action", ["organize", "clean"])
def test_organize_and_clean_need_write_on_the_desktop(env, action):
    (env["desk"] / "a.txt").write_text("x")
    out = desktop.desktop_control({"action": action})
    assert out.startswith(MARK)
    assert [p.name for p in env["desk"].iterdir()] == ["a.txt"]


def test_organize_works_when_desktop_is_rw(env):
    fs_policy.configure(folders=[(env["desk"], "rw")])
    (env["desk"] / "a.txt").write_text("x")
    out = desktop.desktop_control({"action": "organize"})
    assert not out.startswith(MARK)
    assert not (env["desk"] / "a.txt").exists()


def test_free_task_waits_for_confirmation_and_reports(env, monkeypatch):
    calls = []
    code = "\n".join(f"print({i})" for i in range(20))
    monkeypatch.setattr(desktop, "_ask_gemini_for_desktop_action", lambda t: code)
    ran = []
    monkeypatch.setattr(desktop, "_execute_generated_code",
                        lambda c, player=None: ran.append(c) or "Done.")

    def fake_request(key, title, detail, run, *, report=False):
        calls.append((key, title, detail, report))
        return "asked"          # the user has not answered: run() is not called
    monkeypatch.setattr(confirm, "request", fake_request)
    out = desktop.desktop_control({"action": "task", "task": "do it"})
    assert out == "asked" and not ran
    key, title, detail, report = calls[0]
    assert (key, title, report) == ("desktop_task", "Run a desktop task", True)
    assert detail.splitlines() == code.splitlines()[:12]


def test_free_task_runs_when_confirmed(env, monkeypatch):
    monkeypatch.setattr(desktop, "_ask_gemini_for_desktop_action", lambda t: "print(1)")
    monkeypatch.setattr(desktop, "_execute_generated_code", lambda c, player=None: "ran:" + c)
    monkeypatch.setattr(confirm, "request",
                        lambda key, title, detail, run, report=False: run())
    assert desktop.desktop_control({"action": "task", "task": "x"}) == "ran:print(1)"


def test_error_text_from_gemini_is_returned_not_executed(env, monkeypatch):
    # "ERROR: unsafe" is a valid Python annotation, so the lines after it would run with
    # no banner (review of spec 003). It must come back as text, untouched.
    text = "ERROR: unsafe\nopen('pwned.txt', 'w').write('x')"
    monkeypatch.setattr(desktop, "_ask_gemini_for_desktop_action", lambda t: text)
    asked = []
    monkeypatch.setattr(confirm, "request", lambda *a, **k: asked.append(1) or "asked")
    out = desktop.desktop_control({"action": "task", "task": "x"})
    assert out == text and not asked
    assert desktop._execute_generated_code(text) == text
