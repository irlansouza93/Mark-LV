"""The actions obey core/fs_policy: file_controller (spec 003, US1 + US2).

Real files in tmp_path, policy pointed at them with fs_policy.configure(); no real config/.
"""
import sys

import pytest

from actions import file_controller as fc
from core import fs_policy, undo

MARK = "[FOLDER_POLICY]"


@pytest.fixture
def env(tmp_path):
    d = {k: tmp_path / k for k in ("home", "app", "ro", "rw", "outside")}
    for p in d.values():
        p.mkdir()
    d["tmp"] = tmp_path
    fs_policy.configure(folders=[(d["ro"], "ro"), (d["rw"], "rw")], home=d["home"],
                        app_dir=d["app"], env={}, platform=sys.platform, uploaded=None)
    undo._stack.clear()
    yield d
    fs_policy.reset()
    undo._stack.clear()


def run(**p):
    return fc.file_controller(p)


def snapshot(*roots):
    return sorted(str(x) for r in roots for x in r.rglob("*"))


# ── US1 ──────────────────────────────────────────────────────────────────────

def test_file_controller_reads_in_ro(env):
    (env["ro"] / "a.txt").write_text("hello")
    assert "a.txt" in run(action="list", path=str(env["ro"]))
    assert run(action="read", path=str(env["ro"]), name="a.txt") == "hello"
    assert "Name: a.txt" in run(action="info", path=str(env["ro"]), name="a.txt")


def test_file_controller_changes_in_ro_are_refused_and_do_nothing(env):
    (env["ro"] / "a.txt").write_text("hello")
    (env["rw"] / "b.txt").write_text("b")
    before = snapshot(env["ro"], env["rw"])
    ro = str(env["ro"])
    answers = [
        run(action="create_file", path=ro, name="new.txt", content="x"),
        run(action="create_folder", path=ro, name="sub"),
        run(action="write", path=ro, name="a.txt", content="changed"),
        run(action="rename", path=ro, name="a.txt", new_name="z.txt"),
        run(action="delete", path=ro, name="a.txt"),
        run(action="move", path=ro, name="a.txt", destination=str(env["rw"])),            # source is ro
        run(action="move", path=str(env["rw"]), name="b.txt", destination=ro),           # target is ro
        run(action="copy", path=str(env["rw"]), name="b.txt", destination=ro),           # target is ro
    ]
    assert all(a.startswith(MARK) for a in answers), answers
    assert snapshot(env["ro"], env["rw"]) == before
    assert (env["ro"] / "a.txt").read_text() == "hello"


def test_file_controller_copy_ro_to_rw_works(env):
    (env["ro"] / "a.txt").write_text("hello")
    assert run(action="copy", path=str(env["ro"]), name="a.txt", destination=str(env["rw"])).startswith("Copied")
    assert (env["rw"] / "a.txt").read_text() == "hello"


def test_file_controller_outside_is_refused(env):
    (env["outside"] / "a.txt").write_text("x")
    out = run(action="read", path=str(env["outside"]), name="a.txt")
    assert out.startswith(MARK) and "ALLOWED FOLDERS" in out
    assert run(action="list", path=str(env["outside"])).startswith(MARK)
    assert run(action="create_file", path=str(env["outside"]), name="n.txt").startswith(MARK)
    assert not (env["outside"] / "n.txt").exists()


def test_file_controller_write_and_move_in_rw_work(env):
    assert run(action="create_file", path=str(env["rw"]), name="a.txt", content="1").startswith("File created")
    assert run(action="write", path=str(env["rw"]), name="a.txt", content="2").startswith("Written")
    assert run(action="rename", path=str(env["rw"]), name="a.txt", new_name="b.txt").startswith("Renamed")
    (env["rw"] / "d").mkdir()
    assert run(action="move", path=str(env["rw"]), name="b.txt", destination=str(env["rw"] / "d")).startswith("Moved")
    assert (env["rw"] / "d" / "b.txt").read_text() == "2"


def test_find_and_largest_skip_protected_items(env):
    root = env["rw"]
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("1")
    (root / ".git").mkdir()
    (root / ".git" / "main.config").write_text("secret")
    (root / ".env").write_text("main=1")
    (root / "main.kdbx").write_text("vault")
    out = run(action="find", path=str(root), name="main")
    assert "main.py" in out
    assert ".git" not in out and ".env" not in out and "kdbx" not in out and "main.config" not in out
    big = run(action="largest", path=str(root))
    assert "main.py" in big and "kdbx" not in big and ".env" not in big


def test_undo_of_a_create_still_works_after_the_folder_became_read_only(env):
    assert run(action="create_file", path=str(env["rw"]), name="u.txt", content="1").startswith("File created")
    fs_policy.configure(folders=[(env["rw"], "ro")])
    assert (env["rw"] / "u.txt").exists()
    assert "Removed" in undo.undo_last()
    assert not (env["rw"] / "u.txt").exists()


def test_disk_usage_stays_outside_the_policy(env):
    assert run(action="disk_usage", path=str(env["outside"])).startswith("Disk usage")


def test_is_safe_path_delegates(env):
    assert fc._is_safe_path(env["rw"] / "x") is True
    assert fc._is_safe_path(env["outside"] / "x") is False
    assert fc._is_safe_path(env["ro"] / "x") is True        # reading is enough to be "safe to look at"


# ── US2 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def secrets(env):
    """Everything allowed read-write, so only the deny list can say no."""
    fs_policy.configure(folders=[(env["tmp"], "rw")])
    cfgdir = env["app"] / "config"
    (cfgdir / "certs").mkdir(parents=True)
    (cfgdir / "api_keys.json").write_text('{"gemini_api_key": "SECRET"}')
    (cfgdir / "ok.txt").write_text("fine")
    (env["home"] / ".ssh").mkdir()
    (env["home"] / ".ssh" / "id_ed25519").write_text("PRIVATE")
    return env


def test_denied_read_copy_move_list_never_touch_the_content(secrets, monkeypatch):
    touched = []
    import shutil
    from pathlib import Path

    def spy(name):
        def f(*a, **k):
            touched.append(name)
            raise AssertionError(f"{name} must not run on a protected item")
        return f
    monkeypatch.setattr(Path, "read_text", spy("read_text"))
    monkeypatch.setattr(shutil, "copy2", spy("copy2"))
    monkeypatch.setattr(shutil, "move", spy("move"))

    cfgdir, ssh = str(secrets["app"] / "config"), str(secrets["home"] / ".ssh")
    out = [
        run(action="read", path=cfgdir, name="api_keys.json"),
        run(action="read", path=ssh, name="id_ed25519"),
        run(action="copy", path=cfgdir, name="api_keys.json", destination=str(secrets["rw"])),
        run(action="move", path=cfgdir, name="api_keys.json", destination=str(secrets["rw"])),
        run(action="list", path=ssh),
        run(action="info", path=cfgdir, name="api_keys.json"),
        run(action="write", path=cfgdir, name="api_keys.json", content="{}"),
        run(action="delete", path=cfgdir, name="api_keys.json"),
    ]
    assert all(o.startswith(MARK) and "protected location" in o for o in out), out
    assert touched == []
    monkeypatch.undo()
    assert (secrets["app"] / "config" / "api_keys.json").read_text() == '{"gemini_api_key": "SECRET"}'


def test_denied_list_does_not_show_secrets(secrets):
    listing = run(action="list", path=str(secrets["app"] / "config"))
    assert "ok.txt" in listing
    assert "api_keys.json" not in listing and "certs" not in listing
    assert "SECRET" not in listing


def test_denied_copy_of_a_folder_with_secrets_is_refused_or_filtered(secrets):
    """Copying the config folder as a whole must not smuggle the key out."""
    out = run(action="copy", path=str(secrets["app"]), name="config", destination=str(secrets["rw"]))
    assert out.startswith(MARK) or not list(secrets["rw"].rglob("api_keys.json"))


# ── review fixes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["ABS", r"..\x", "../x", "sub/x", r"sub\x"])
def test_rename_refuses_new_name_that_is_not_a_bare_name(env, bad):
    (env["rw"] / "a.txt").write_text("a")
    new = str(env["outside"] / "x.txt") if bad == "ABS" else bad
    (env["rw"] / "sub").mkdir()
    out = run(action="rename", path=str(env["rw"]), name="a.txt", new_name=new)
    assert (env["rw"] / "a.txt").exists()
    assert not (env["outside"] / "x.txt").exists()
    assert not (env["tmp"] / "x").exists() and not (env["rw"] / "sub" / "x").exists()
    assert "bare name" in out


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only")
def test_copy_folder_does_not_follow_junctions(env):
    import _winapi
    (env["outside"] / "secret.txt").write_text("s")
    (env["rw"] / "proj").mkdir()
    (env["rw"] / "proj" / "ok.txt").write_text("ok")
    _winapi.CreateJunction(str(env["outside"]), str(env["rw"] / "proj" / "jn"))
    out = run(action="copy", path=str(env["rw"]), name="proj", destination=str(env["rw"] / "copy"))
    assert (env["rw"] / "copy" / "ok.txt").exists()
    assert not (env["rw"] / "copy" / "jn").exists()
    assert "jn" in out
