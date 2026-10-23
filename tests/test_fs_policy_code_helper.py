"""code_helper obeys core/fs_policy and the run confirmation (spec 003, T019).

Real files in tmp_path; subprocess.run and the Gemini model are fakes.
"""
import sys
import threading
import types

import pytest

from actions import code_helper as ch
from core import confirm, fs_policy

MARK = "[FOLDER_POLICY]"


class FakeModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    def generate_content(self, prompt):
        self.calls += 1
        return types.SimpleNamespace(text=self.texts.pop(0) if self.texts else "print('x')")


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = {k: tmp_path / k for k in ("home", "app", "ro", "rw", "outside")}
    for p in d.values():
        p.mkdir()
    fs_policy.configure(folders=[(d["ro"], "ro"), (d["rw"], "rw")], home=d["home"],
                        app_dir=d["app"], env={}, platform=sys.platform, uploaded=None)
    monkeypatch.setattr(ch, "DESKTOP", d["rw"])
    runs = []

    def fake_run(cmd, **kw):
        runs.append(cmd)
        return types.SimpleNamespace(stdout="hi", stderr="", returncode=0)

    monkeypatch.setattr(ch.subprocess, "run", fake_run)
    d["runs"] = runs
    model = FakeModel([])
    monkeypatch.setattr(ch, "_get_gemini", lambda *a, **k: model)
    d["model"] = model

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


def test_explain_outside_is_refused_without_reading(env):
    f = env["outside"] / "a.py"
    f.write_text("print(1)")
    out = ch.code_helper({"action": "explain", "file_path": str(f)})
    assert out.startswith(MARK)
    assert env["model"].calls == 0


def test_explain_in_ro_reads(env):
    f = env["ro"] / "a.py"
    f.write_text("print(1)")
    env["model"].texts = ["It prints."]
    assert ch.code_helper({"action": "explain", "file_path": str(f)}) == "It prints."


def test_write_in_ro_is_refused_and_writes_nothing(env):
    out = ch.code_helper({"action": "write", "description": "x", "output_path": str(env["ro"] / "n.py")})
    assert out.startswith(MARK)
    assert not (env["ro"] / "n.py").exists()
    assert env["model"].calls == 0


def test_write_default_goes_to_desktop_when_allowed(env):
    out = ch.code_helper({"action": "write", "description": "x"})
    assert "Saved to" in out
    assert (env["rw"] / "jarvis_code.py").exists()


def test_write_default_desktop_not_allowed_is_refused(env, monkeypatch):
    monkeypatch.setattr(ch, "DESKTOP", env["outside"])
    out = ch.code_helper({"action": "write", "description": "x"})
    assert out.startswith(MARK)
    assert not any(env["outside"].iterdir())


def test_edit_in_ro_is_refused_and_file_untouched(env):
    f = env["ro"] / "a.py"
    f.write_text("print(1)")
    out = ch.code_helper({"action": "edit", "file_path": str(f), "description": "change"})
    assert out.startswith(MARK)
    assert f.read_text() == "print(1)"


def test_optimize_with_output_path_in_ro_is_refused(env):
    out = ch.code_helper({"action": "optimize", "code": "print(1)",
                          "output_path": str(env["ro"] / "o.py")})
    assert out.startswith(MARK)
    assert not (env["ro"] / "o.py").exists()


def test_run_in_ro_is_refused_and_nothing_runs(env):
    f = env["ro"] / "a.py"
    f.write_text("print(1)")
    out = ch.code_helper({"action": "run", "file_path": str(f)})
    assert out.startswith(MARK)
    assert env["runs"] == []


def test_run_in_rw_waits_for_confirmation(env):
    f = env["rw"] / "a.py"
    f.write_text("print(1)")
    out = ch.code_helper({"action": "run", "file_path": str(f)})
    assert "[CONFIRMATION_PENDING]" in out
    assert env["runs"] == []
    assert ("show", "Run a.py", f"{sys.executable} {f}") in env["got"]
    confirm.resolve(True)
    assert env["ev"].wait(2.0)
    assert env["runs"] and env["runs"][0][-1] == str(f)
    assert env["got"][-1].startswith("[CONFIRMED_RESULT] Run a.py") and "<<<" + chr(10) + "Output:" in env["got"][-1]


def test_run_cancelled_never_runs(env):
    f = env["rw"] / "a.py"
    f.write_text("print(1)")
    ch.code_helper({"action": "run", "file_path": str(f)})
    confirm.resolve(False)
    assert env["ev"].wait(2.0)
    assert env["runs"] == []


def test_build_writes_then_confirms_whole_cycle(env):
    env["model"].texts = ["print('ok')"]
    out = ch.code_helper({"action": "build", "description": "say ok",
                          "output_path": str(env["rw"] / "b.py")})
    assert "[CONFIRMATION_PENDING]" in out
    assert (env["rw"] / "b.py").read_text() == "print('ok')"
    assert env["runs"] == []
    confirm.resolve(True)
    assert env["ev"].wait(2.0)
    assert len(env["runs"]) == 1
    assert "Build complete" in env["got"][-1]


def test_build_in_ro_is_refused_before_anything(env):
    out = ch.code_helper({"action": "build", "description": "x",
                          "output_path": str(env["ro"] / "b.py")})
    assert out.startswith(MARK)
    assert env["model"].calls == 0 and env["runs"] == []


def test_desktop_shortcut_resolves_to_the_real_desktop_not_the_cwd(env, monkeypatch):
    # Found in the real test (2026-10-23): "desktop/jarvis_ola.py" was resolved against the
    # process CWD (the app folder), so the run was refused for the wrong folder.
    from actions import file_controller as fc
    monkeypatch.setattr(fc, "_get_desktop", lambda: env["rw"])
    (env["rw"] / "ola.py").write_text("print(1)")
    out = ch.code_helper({"action": "run", "file_path": "desktop/ola.py"})
    assert "[CONFIRMATION_PENDING]" in out
    confirm.resolve(False)


def test_bare_relative_file_path_is_looked_up_on_the_desktop(env):
    (env["rw"] / "b.py").write_text("print(2)")
    out = ch.code_helper({"action": "run", "file_path": "b.py"})
    assert "[CONFIRMATION_PENDING]" in out
    confirm.resolve(False)
