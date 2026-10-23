"""dev_agent obeys core/fs_policy and the install+run confirmation (spec 003, T023).

Real files in tmp_path; subprocess, the planner and the Gemini model are fakes.
"""
import sys
import threading
import types

import pytest

from actions import dev_agent as da
from core import confirm, fs_policy

MARK = "[FOLDER_POLICY]"


class FakeModel:
    def generate_content(self, prompt):
        return types.SimpleNamespace(text="print('hi')")


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = {k: tmp_path / k for k in ("home", "app", "ro", "rw")}
    for p in d.values():
        p.mkdir()
    fs_policy.configure(folders=[(d["ro"], "ro"), (d["rw"], "rw")], home=d["home"],
                        app_dir=d["app"], env={}, platform=sys.platform, uploaded=None)
    monkeypatch.setattr(da, "PROJECTS_DIR", d["rw"] / "Projects")
    monkeypatch.setattr(da, "_get_model", lambda *a, **k: FakeModel())
    monkeypatch.setattr(da.time, "sleep", lambda s: None)
    d["plan"] = {"project_name": "demo", "entry_point": "main.py", "run_command": "python main.py",
                 "dependencies": ["requests"],
                 "files": [{"path": "main.py", "description": "entry", "imports": []}]}
    monkeypatch.setattr(da, "_plan_project", lambda desc, lang: d["plan"])
    vs = []
    monkeypatch.setattr(da, "_open_vscode", lambda pd: vs.append(pd) or True)
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return types.SimpleNamespace(stdout="hi", stderr="", returncode=1 if "show" in cmd else 0)

    monkeypatch.setattr(da.subprocess, "run", fake_run)
    d["runs"], d["vscode"] = runs, vs

    got, ev = [], threading.Event()

    def deliver(text):
        got.append(text)
        ev.set()

    confirm._pending = None
    confirm.bind(lambda t, dd: got.append(("show", t, dd)), lambda: None, deliver=deliver)
    d["got"], d["ev"] = got, ev
    yield d
    fs_policy.reset()
    confirm._pending = None
    confirm.bind(None, None)


def build():
    return da.dev_agent({"description": "demo app"})


def test_projects_dir_outside_is_refused_and_nothing_created(env, monkeypatch):
    monkeypatch.setattr(da, "PROJECTS_DIR", env["home"] / "Projects")
    assert build().startswith(MARK)
    assert not (env["home"] / "Projects").exists()


def test_projects_dir_in_ro_is_refused(env, monkeypatch):
    monkeypatch.setattr(da, "PROJECTS_DIR", env["ro"] / "Projects")
    assert build().startswith(MARK)
    assert not (env["ro"] / "Projects").exists()


def test_generates_files_then_waits_for_one_confirmation(env):
    out = build()
    assert (env["rw"] / "Projects" / "demo" / "main.py").read_text() == "print('hi')"
    assert "[CONFIRMATION_PENDING]" in out and "generated" in out
    assert env["runs"] == []            # no pip, no run yet
    assert env["vscode"]                # opening the editor is not confirmed
    shows = [g for g in env["got"] if isinstance(g, tuple)]
    assert len(shows) == 1
    assert shows[0][1] == "Install and run demo"
    assert "requests" in shows[0][2] and "python main.py" in shows[0][2]


def test_confirm_installs_and_runs(env):
    build()
    confirm.resolve(True)
    assert env["ev"].wait(2.0)
    assert any("pip" in c for c in env["runs"]) and any(c[-1] == "main.py" for c in env["runs"])
    assert env["got"][-1].startswith("[CONFIRMED_RESULT] Install and run demo:")


def test_cancel_runs_nothing(env):
    build()
    confirm.resolve(False)
    assert env["ev"].wait(2.0)
    assert env["runs"] == []


def test_escaping_file_is_skipped_named_and_the_rest_goes_on(env):
    env["plan"]["files"] += [{"path": "../evil.py", "description": "x", "imports": []},
                             {"path": "/abs/evil2.py", "description": "x", "imports": []}]
    out = build()
    proj = env["rw"] / "Projects"
    assert (proj / "demo" / "main.py").exists()
    assert not (proj / "evil.py").exists()
    assert "../evil.py" in out and "/abs/evil2.py" in out
    assert "[CONFIRMATION_PENDING]" in out


def test_fix_files_cannot_escape_either(env):
    proj = env["rw"] / "Projects" / "demo"
    proj.mkdir(parents=True)
    da._fix_files("boom", "d", [], {"../evil.py": "x"}, "python", proj, "../evil.py")
    assert not (env["rw"] / "Projects" / "evil.py").exists()
