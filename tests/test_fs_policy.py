"""core/fs_policy.py — the one place that decides which paths the assistant may touch.

Everything runs on tmp_path with fs_policy.configure(...): no real config/, no network, no UI.
"""
import json
import os
import sys
from pathlib import Path

import pytest

from core import errlog, fs_policy
from core.fs_policy import PolicyDenied


@pytest.fixture
def env(tmp_path):
    """A fake machine: home, app dir and a few folders, policy pointed at them."""
    d = {k: tmp_path / k for k in ("home", "app", "ro", "rw", "dev", "development", "outside")}
    for p in d.values():
        p.mkdir()
    d["tmp"] = tmp_path
    fs_policy.configure(folders=[(d["ro"], "ro"), (d["rw"], "rw")], home=d["home"],
                        app_dir=d["app"], env={}, platform=sys.platform, uploaded=None)
    yield d
    fs_policy.reset()


def _folders(*pairs):
    fs_policy.configure(folders=[(p, m) for p, m in pairs])


# ── T002: basic decision ────────────────────────────────────────────────────

def test_read_in_ro_is_ok(env):
    d = fs_policy.check(env["ro"] / "a.txt", "read", actor="t")
    assert d.allowed and d.reason == "ok" and d.root == env["ro"].resolve()


def test_write_and_exec_in_ro_are_read_only(env):
    for op in ("write", "exec"):
        d = fs_policy.check(env["ro"] / "a.txt", op, actor="t")
        assert not d.allowed and d.reason == "read_only"
        assert d.root == env["ro"].resolve() and d.suggest == env["ro"].resolve()


def test_write_in_rw_is_ok(env):
    assert fs_policy.check(env["rw"] / "a.txt", "write", actor="t").reason == "ok"


def test_outside_has_a_suggestion(env):
    d = fs_policy.check(env["outside"] / "deep" / "a.txt", "read", actor="t")
    assert not d.allowed and d.reason == "outside"
    assert d.suggest is not None and d.suggest.exists()


def test_most_specific_folder_wins(env):
    sub = env["ro"] / "sub"
    sub.mkdir()
    _folders((env["ro"], "ro"), (sub, "rw"))
    assert fs_policy.check(sub / "x.txt", "write", actor="t").allowed
    assert fs_policy.check(env["ro"] / "x.txt", "write", actor="t").reason == "read_only"
    # order in the list must not matter
    _folders((sub, "rw"), (env["ro"], "ro"))
    assert fs_policy.check(sub / "x.txt", "write", actor="t").allowed


def test_sibling_with_common_prefix_is_not_covered(env):
    _folders((env["dev"], "rw"))
    assert fs_policy.check(env["dev"] / "a", "read", actor="t").allowed
    assert fs_policy.check(env["development"] / "a", "read", actor="t").reason == "outside"


def test_nonexistent_file_inside_rw_is_ok(env):
    assert fs_policy.check(env["rw"] / "new" / "deeper" / "n.txt", "write", actor="t").allowed


def test_dotdot_escape_is_outside(env):
    d = fs_policy.check(env["rw"] / ".." / "x", "write", actor="t")
    assert d.reason == "outside"


def test_invalid_path_does_not_raise(env):
    d = fs_policy.check("a\0b", "read", actor="t")
    assert not d.allowed and d.reason == "invalid"
    assert not fs_policy.check("", "read", actor="t").allowed


@pytest.mark.skipif(sys.platform != "win32", reason="UNC paths are a Windows thing")
def test_unc_share_is_covered_by_string():
    try:
        fs_policy.configure(folders=[(r"\\server\share", "rw")], home=r"C:\nohome",
                            app_dir=r"C:\noapp", env={}, platform="win32", uploaded=None)
        assert fs_policy.check(r"\\server\share\a.txt", "read", actor="t").allowed
        assert fs_policy.check(r"\\server\other\a.txt", "read", actor="t").reason == "outside"
    finally:
        fs_policy.reset()


def test_require_returns_normalised_path_and_denies_with_sentence(env):
    got = fs_policy.require(env["rw"] / "x" / ".." / "a.txt", "write", actor="t")
    assert got == (env["rw"] / "a.txt").resolve()
    with pytest.raises(PolicyDenied) as ei:
        fs_policy.require(env["outside"] / "a.txt", "read", actor="t")
    msg = str(ei.value)
    assert msg.startswith("[FOLDER_POLICY] I did not read ")
    assert "outside the folders they allowed" in msg and "ALLOWED FOLDERS" in msg
    assert "Do not try another path" in msg
    with pytest.raises(PolicyDenied) as ei:
        fs_policy.require(env["ro"] / "a.txt", "write", actor="t")
    assert "I did not change " in str(ei.value) and "allowed as read-only" in str(ei.value)
    with pytest.raises(PolicyDenied) as ei:
        fs_policy.require(env["ro"] / "a.txt", "exec", actor="t")
    assert "I did not run " in str(ei.value)
    with pytest.raises(PolicyDenied) as ei:
        fs_policy.require("a\0b", "read", actor="t")
    assert "not valid" in str(ei.value) and ei.value.decision.reason == "invalid"


# ── T003: deny list ─────────────────────────────────────────────────────────

def _deny_env(env):
    """Everything liberated read-write so only the deny list can say no."""
    _folders((env["tmp"], "rw"))
    return env


@pytest.mark.parametrize("rel", [
    "app/config/api_keys.json",
    "app/config/certs/x.pem",
    "app/config/whatsapp_web/a",
    "app/memory/long_term.json",
    "home/.ssh/id_ed25519",
    "home/.aws/credentials",
    "somewhere/cofre.kdbx",
    "proj/.git/config",
    "proj/.env",
    "proj/.env.production",
    "outro_clone/config/api_keys.json",
    "app/CONFIG/API_KEYS.JSON",
])
def test_denied_everywhere(env, rel):
    _deny_env(env)
    for op in ("read", "write", "exec"):
        d = fs_policy.check(env["tmp"] / rel, op, actor="t")
        assert not d.allowed and d.reason == "denied" and d.deny_reason, (rel, op)
    assert fs_policy.is_denied(env["tmp"] / rel)


@pytest.mark.parametrize("rel", ["proj/.env.example", "proj/.gitignore", "proj/.github/x.yml",
                                 "proj/notes.txt", "home/.sshfake/x"])
def test_not_denied(env, rel):
    _deny_env(env)
    assert fs_policy.is_denied(env["tmp"] / rel) is None
    assert fs_policy.check(env["tmp"] / rel, "read", actor="t").allowed


def test_denied_sentence_names_the_reason(env):
    _deny_env(env)
    with pytest.raises(PolicyDenied) as ei:
        fs_policy.require(env["home"] / ".ssh" / "id_ed25519", "read", actor="t")
    assert "protected location" in str(ei.value) and "always off limits" in str(ei.value)


def test_validate_folder(env):
    assert fs_policy.validate_folder(env["home"] / ".ssh")
    assert fs_policy.validate_folder(env["app"] / "config" / "certs")
    assert fs_policy.validate_folder(env["home"]) is None
    assert fs_policy.validate_folder(env["rw"]) is None


def test_platform_rules(tmp_path):
    home = tmp_path / "home"
    try:
        fs_policy.configure(folders=[(tmp_path, "rw")], home=home, app_dir=tmp_path / "app",
                            env={}, platform="darwin", uploaded=None)
        assert fs_policy.is_denied(home / "Library" / "Keychains" / "login.keychain-db")
        assert fs_policy.is_denied(home / ".local" / "share" / "keyrings" / "x") is None
        fs_policy.configure(platform="linux")
        assert fs_policy.is_denied(home / ".local" / "share" / "keyrings" / "x")
        assert fs_policy.is_denied(home / "Library" / "Keychains" / "x") is None
        fs_policy.configure(platform="win32", env={"APPDATA": str(tmp_path / "ad")})
        assert fs_policy.is_denied(tmp_path / "ad" / "Microsoft" / "Credentials" / "x")
    finally:
        fs_policy.reset()


def test_deny_rules_are_listed(env):
    rules = fs_policy.deny_rules()
    assert rules and all(r.kind in ("path", "parts", "name") and r.reason for r in rules)


# ── T004: escapes ───────────────────────────────────────────────────────────

def test_symlink_escape(env):
    (env["home"] / ".ssh").mkdir()
    (env["home"] / ".ssh" / "id_ed25519").write_text("k")
    (env["outside"] / "f.txt").write_text("x")
    try:
        os.symlink(env["outside"], env["rw"] / "lnk_out", target_is_directory=True)
        os.symlink(env["home"] / ".ssh", env["rw"] / "lnk_ssh", target_is_directory=True)
    except OSError:
        pytest.skip("no privilege to create symlinks")
    _folders((env["rw"], "rw"))
    assert fs_policy.check(env["rw"] / "lnk_out" / "f.txt", "read", actor="t").reason == "outside"
    assert fs_policy.check(env["rw"] / "lnk_ssh" / "id_ed25519", "read", actor="t").reason == "denied"


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows only")
def test_junction_escape(env):
    import _winapi
    (env["home"] / ".ssh").mkdir()
    (env["home"] / ".ssh" / "id_ed25519").write_text("k")
    (env["outside"] / "f.txt").write_text("x")
    _winapi.CreateJunction(str(env["outside"]), str(env["rw"] / "jn_out"))
    _winapi.CreateJunction(str(env["home"] / ".ssh"), str(env["rw"] / "jn_ssh"))
    _folders((env["rw"], "rw"))
    assert fs_policy.check(env["rw"] / "jn_out" / "f.txt", "read", actor="t").reason == "outside"
    assert fs_policy.check(env["rw"] / "jn_ssh" / "id_ed25519", "read", actor="t").reason == "denied"


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 names are Windows only")
def test_short_name_form_is_denied(env):
    import ctypes
    (env["home"] / ".ssh").mkdir()
    f = env["home"] / ".ssh" / "id_ed25519"
    f.write_text("k")
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(f), buf, 1024)
    if not n or buf.value.lower() == str(f).lower():
        pytest.skip("8.3 names are disabled on this volume")
    _folders((env["home"], "rw"))
    assert fs_policy.check(buf.value, "read", actor="t").reason == "denied"


def test_confined(env):
    base = env["rw"] / "proj"
    base.mkdir()
    for bad in ("../x.py", "a/../../x", "C:/x.py", "/x.py", "\\x.py"):
        with pytest.raises(PolicyDenied):
            fs_policy.confined(base, bad)
    assert fs_policy.confined(base, "utils/h.py") == (base / "utils" / "h.py").resolve()


def test_confined_blocks_a_link_that_points_out(env):
    base = env["rw"] / "proj"
    base.mkdir()
    try:
        os.symlink(env["outside"], base / "lnk", target_is_directory=True)
    except OSError:
        pytest.skip("no privilege to create symlinks")
    with pytest.raises(PolicyDenied):
        fs_policy.confined(base, "lnk/x.py")


# ── T005: state ─────────────────────────────────────────────────────────────

def test_uploaded_file(env):
    f = env["outside"] / "up.txt"
    f.write_text("x")
    fs_policy.bind_uploaded(lambda: str(f))
    assert fs_policy.check(f, "read", actor="t").reason == "uploaded"
    assert fs_policy.check(f, "write", actor="t").reason == "outside"
    assert fs_policy.check(env["outside"] / "other.txt", "read", actor="t").reason == "outside"
    fs_policy.bind_uploaded(None)
    assert fs_policy.check(f, "read", actor="t").reason == "outside"


def test_uploaded_file_inside_a_denied_item_stays_denied(env):
    f = env["home"] / ".ssh" / "id_ed25519"
    f.parent.mkdir()
    f.write_text("k")
    fs_policy.bind_uploaded(lambda: str(f))
    assert fs_policy.check(f, "read", actor="t").reason == "denied"


def test_walk_prunes_secrets_and_does_not_follow_links(env):
    root = env["rw"]
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("1")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("c")
    (env["home"] / ".ssh").mkdir()
    (env["home"] / ".ssh" / "k").write_text("k")
    (env["home"] / "mine.txt").write_text("m")
    (root / "cofre.kdbx").write_text("v")
    (root / ".env").write_text("K=1")
    (root / ".env.example").write_text("K=")
    (env["outside"] / "far.txt").write_text("x")
    try:
        os.symlink(env["outside"], root / "lnk", target_is_directory=True)
    except OSError:
        pass
    seen = []
    for dp, dirs, files in fs_policy.walk(root, actor="t"):
        for f in files:
            seen.append(Path(dp, f).name)
    assert "a.py" in seen and ".env.example" in seen
    assert "config" not in seen
    home_seen = [Path(dp, f).name for dp, _d, fs in fs_policy.walk(env["home"], actor="t") for f in fs]
    assert home_seen == ["mine.txt"]      # .ssh pruned
    assert "cofre.kdbx" not in seen and ".env" not in seen and "far.txt" not in seen


def test_reload_changes_the_next_decision(env):
    src = [(env["ro"], "ro")]
    fs_policy.configure(folders=src)
    assert fs_policy.check(env["ro"] / "a", "write", actor="t").reason == "read_only"
    src[:] = [(env["ro"], "rw")]
    # cached until reload: a decision must not hit the config file every time
    assert fs_policy.check(env["ro"] / "a", "write", actor="t").reason == "read_only"
    fs_policy.reload()
    assert fs_policy.check(env["ro"] / "a", "write", actor="t").allowed


def test_every_refusal_is_logged_and_permissions_are_not(env, monkeypatch):
    calls = []
    monkeypatch.setattr(errlog, "record_failure", lambda *a, **k: calls.append((a, k)))
    fs_policy.check(env["rw"] / "ok.txt", "write", actor="x")
    assert calls == []
    fs_policy.check(env["outside"] / "a", "read", actor="file_controller")
    fs_policy.check(env["ro"] / "a", "write", actor="code_helper")
    fs_policy.check(env["home"] / ".ssh" / "k", "read", actor="t")
    _a, k = calls[0]
    assert k["component"] == "fs_policy" and k["name"] == "file_controller"
    assert k["error_type"] == "policy:outside" and k["message"].startswith("read ")
    assert [c[1]["error_type"] for c in calls] == ["policy:outside", "policy:read_only", "policy:denied"]
    assert "root" in calls[1][1]["context"] and calls[2][1]["context"]["reason"] == "denied"


def test_works_without_errlog(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("log is down")
    monkeypatch.setattr(errlog, "record_failure", boom)
    assert not fs_policy.check(env["outside"] / "a", "read", actor="t").allowed


# ── T009: errlog kind ───────────────────────────────────────────────────────

def test_errlog_kind_for_policy():
    assert errlog._kind_for("fs_policy", "policy:outside") == "policy"
    assert errlog._kind_for("fs_policy", None) == "policy"
    assert errlog._kind_for("tool", "soft:x") == "soft"


# ── T010: config_manager side ───────────────────────────────────────────────

@pytest.fixture
def cfg(tmp_path, monkeypatch):
    from memory import config_manager as cm
    monkeypatch.setattr(cm, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cm, "CONFIG_FILE", tmp_path / "cfg" / "api_keys.json")
    monkeypatch.setattr(errlog, "record_failure", lambda *a, **k: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    fs_policy.configure(folders=None, home=tmp_path / "home", app_dir=tmp_path / "app",
                        env={}, platform=sys.platform, uploaded=None)
    (tmp_path / "home").mkdir()
    yield cm
    fs_policy.reset()


def _write_cfg(cm, data):
    cm.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cm.CONFIG_FILE.write_text(json.dumps(data), encoding="utf-8")


def test_missing_key_gives_default_without_writing(cfg, tmp_path):
    got = cfg.get_allowed_folders()
    assert got == [{"path": str(Path.home()), "mode": "rw"}]
    assert cfg.allowed_folders_error() is None
    assert not cfg.CONFIG_FILE.exists()


def test_invalid_items_are_dropped_and_mode_falls_back_to_ro(cfg, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write_cfg(cfg, {"allowed_folders": [
        {"path": str(a), "mode": "rw"}, {"mode": "rw"}, "junk",
        {"path": str(b), "mode": "banana"}, {"path": 5, "mode": "ro"}]})
    got = cfg.get_allowed_folders()
    assert [(Path(g["path"]).name, g["mode"]) for g in got] == [("a", "rw"), ("b", "ro")]
    assert cfg.allowed_folders_error() is None


def test_nothing_valid_falls_back_to_default_and_says_so(cfg):
    _write_cfg(cfg, {"allowed_folders": [{"mode": "rw"}, 3]})
    assert cfg.get_allowed_folders() == [{"path": str(Path.home()), "mode": "rw"}]
    assert cfg.allowed_folders_error()
    _write_cfg(cfg, {"allowed_folders": "everything"})
    assert cfg.get_allowed_folders() == [{"path": str(Path.home()), "mode": "rw"}]
    assert cfg.allowed_folders_error()
    _write_cfg(cfg, {"allowed_folders": [{"path": str(Path.home()), "mode": "ro"}]})
    cfg.get_allowed_folders()
    assert cfg.allowed_folders_error() is None


def test_duplicates_collapse_and_last_mode_wins(cfg, tmp_path):
    p = tmp_path / "Dup"
    _write_cfg(cfg, {"allowed_folders": [{"path": str(p), "mode": "rw"},
                                         {"path": str(p).upper() if sys.platform == "win32" else str(p),
                                          "mode": "ro"}]})
    got = cfg.get_allowed_folders()
    assert len(got) == 1 and got[0]["mode"] == "ro"


def test_save_refuses_protected_folder_and_reloads_policy(cfg, tmp_path):
    ssh = tmp_path / "home" / ".ssh"
    msg = cfg.save_allowed_folders([{"path": str(ssh), "mode": "rw"}])
    assert msg and "protected" in msg
    assert not cfg.CONFIG_FILE.exists() or "allowed_folders" not in json.loads(cfg.CONFIG_FILE.read_text())

    proj = tmp_path / "proj"
    proj.mkdir()
    # a source of folders read through config_manager, as in the app
    fs_policy.configure(folders=None)
    assert fs_policy.check(proj / "a", "read", actor="t").reason == "outside"
    assert cfg.save_allowed_folders([{"path": str(proj), "mode": "ro", "label": "P"}]) is None
    saved = json.loads(cfg.CONFIG_FILE.read_text())["allowed_folders"]
    assert saved[0]["mode"] == "ro" and saved[0]["label"] == "P"
    # no restart: the next decision already sees the new list
    assert fs_policy.check(proj / "a", "read", actor="t").allowed
    assert fs_policy.check(proj / "a", "write", actor="t").reason == "read_only"


def test_save_empty_is_refused(cfg):
    assert cfg.save_allowed_folders([])


def test_walk_gate_matches_the_per_name_rules(env):
    """The per-directory regex is only a shortcut: it must hide exactly what is_denied hides."""
    root = env["rw"]
    names = ["ID_RSA", "id_rsa.pub", "k.PEM", "a.key", ".ENV.local", ".env.example", "pw.KDBX",
             "keep.txt", "id_rsa_notes.md", "x.pem.bak", "a.p12", "okay.py"]
    for n in names:
        (root / n).write_text("x")
    shown = {f for _dp, _d, fs in fs_policy.walk(root, actor="t") for f in fs}
    expected = {n for n in names if not fs_policy.is_denied(root / n)}
    assert shown == expected
    assert {"keep.txt", "okay.py", ".env.example", "x.pem.bak"} <= shown
    assert not ({"ID_RSA", "k.PEM", ".ENV.local", "pw.KDBX", "a.p12", "a.key"} & shown)
