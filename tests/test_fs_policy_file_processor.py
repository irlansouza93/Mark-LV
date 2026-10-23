"""file_processor obeys core/fs_policy (spec 003, T020): reads, result files, archive extract."""
import sys
import zipfile

import pytest

from actions import file_processor as fp
from core import fs_policy

MARK = "[FOLDER_POLICY]"


@pytest.fixture
def env(tmp_path):
    d = {k: tmp_path / k for k in ("home", "app", "ro", "rw", "outside")}
    for p in d.values():
        p.mkdir()
    fs_policy.configure(folders=[(d["ro"], "ro"), (d["rw"], "rw")], home=d["home"],
                        app_dir=d["app"], env={}, platform=sys.platform, uploaded=None)
    yield d
    fs_policy.reset()


def test_file_outside_and_not_uploaded_is_refused(env):
    f = env["outside"] / "a.txt"
    f.write_text("secret words")
    out = fp.file_processor({"file_path": str(f), "action": "word_count"})
    assert out.startswith(MARK)


def test_uploaded_file_outside_is_read(env):
    f = env["outside"] / "a.txt"
    f.write_text("one two three")
    fs_policy.configure(uploaded=lambda: str(f))
    out = fp.file_processor({"file_path": str(f), "action": "word_count"})
    assert not out.startswith(MARK)


def test_result_file_in_read_only_folder_is_refused(env):
    f = env["ro"] / "a.txt"
    f.write_text("one two three")
    out = fp.file_processor({"file_path": str(f), "action": "extract_text"})
    assert MARK in out
    assert [p.name for p in env["ro"].iterdir()] == ["a.txt"]


def test_result_file_in_rw_folder_is_written(env):
    f = env["rw"] / "a.txt"
    f.write_text("one two three")
    out = fp.file_processor({"file_path": str(f), "action": "extract_text"})
    assert MARK not in out
    assert (env["rw"] / "a_extracted.txt").exists()


def test_extract_to_outside_destination_is_refused(env):
    z = env["rw"] / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ok.txt", "x")
    dest = env["outside"] / "out"
    out = fp.file_processor({"file_path": str(z), "action": "extract", "destination": str(dest)})
    assert MARK in out
    assert not dest.exists()


def test_extract_skips_member_that_escapes(env):
    z = env["rw"] / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("ok.txt", "fine")
        zf.writestr("../evil.txt", "bad")
    dest = env["rw"] / "out"
    out = fp.file_processor({"file_path": str(z), "action": "extract", "destination": str(dest)})
    assert (dest / "ok.txt").read_text() == "fine"
    assert not (env["rw"] / "evil.txt").exists()
    assert "evil.txt" in out and "skipped" in out.lower()


def _protected_names():
    return [".git/hooks/pre-commit", ".env", "ok.txt"]


def test_extract_zip_skips_protected_members(env):
    z = env["rw"] / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n in _protected_names():
            zf.writestr(n, "x")
    dest = env["rw"] / "out"
    out = fp.file_processor({"file_path": str(z), "action": "extract", "destination": str(dest)})
    assert (dest / "ok.txt").exists()
    assert not (dest / ".env").exists() and not (dest / ".git").exists()
    assert ".env" in out and "pre-commit" in out


def test_extract_tar_skips_protected_members(env):
    import io, tarfile
    t = env["rw"] / "a.tar"
    with tarfile.open(t, "w") as tf:
        for n in _protected_names():
            ti = tarfile.TarInfo(n)
            ti.size = 1
            tf.addfile(ti, io.BytesIO(b"x"))
    dest = env["rw"] / "out"
    out = fp.file_processor({"file_path": str(t), "action": "extract", "destination": str(dest)})
    assert (dest / "ok.txt").exists()
    assert not (dest / ".env").exists() and not (dest / ".git").exists()
    assert ".env" in out and "pre-commit" in out
