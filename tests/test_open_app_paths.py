"""open_app must not report success for a file path that does not exist.

Seen in the real app on 2026-10-17: "abra o arquivo C:\\não\\arquivo_existe.txt"
came back as "Opened C:\\não\\arquivo_existe.txt." — the drive-letter colon sent
it down the `start <name>` branch, and `start` returns before Windows shows its
own "file not found" dialog.
"""
from actions import open_app as oa


def _no_launch(name):
    raise AssertionError(f"launcher must not run for a missing file: {name}")


def test_missing_absolute_path_is_reported(monkeypatch, tmp_path):
    monkeypatch.setitem(oa._OS_LAUNCHERS, oa._SYSTEM, _no_launch)
    missing = tmp_path / "não" / "arquivo_existe.txt"
    out = oa.open_app({"app_name": str(missing)})
    assert not out.startswith("Opened")
    assert "does not exist" in out


def test_missing_windows_path_detected_on_any_os():
    assert oa._file_path_of(r"C:\não\arquivo_existe.txt") is not None
    assert oa._file_path_of(r'"C:\pasta com espaço\x.txt"') is not None
    assert oa._file_path_of(r"\\server\share\x.docx") is not None


def test_existing_path_opens_with_its_associated_app(monkeypatch, tmp_path):
    f = tmp_path / "exists.txt"
    f.write_text("x", encoding="utf-8")
    seen = []
    monkeypatch.setitem(oa._OS_LAUNCHERS, oa._SYSTEM, _no_launch)    # never the shell `start`
    monkeypatch.setattr(oa, "_open_file", lambda p: seen.append(p) or True)
    assert oa.open_app({"app_name": str(f)}).startswith("Opened")
    assert seen


def test_app_names_and_uris_are_not_paths():
    for name in ("WhatsApp", "Chrome", "libreoffice --writer", "ms-settings:",
                 "https://example.com/a/b", "notepad.exe"):
        assert oa._file_path_of(name) is None, name


# ── triage 2026-10-23: "abra desktop/pdf-dark/dist/pdf-dark.exe" became a web search ──
def test_desktop_shortcut_is_a_path(monkeypatch, tmp_path):
    from core import user_dirs
    exe = tmp_path / "pdf-dark" / "dist" / "pdf-dark.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(user_dirs, "desktop", lambda: tmp_path)
    assert oa._file_path_of("desktop/pdf-dark/dist/pdf-dark.exe") == str(exe)
    assert oa._file_path_of(r"Desktop\pdf-dark\dist\pdf-dark.exe") == str(exe)
    seen = []
    monkeypatch.setitem(oa._OS_LAUNCHERS, oa._SYSTEM, _no_launch)
    monkeypatch.setattr(oa, "_open_file", lambda p: seen.append(p) or True)
    assert oa.open_app({"app_name": "desktop/pdf-dark/dist/pdf-dark.exe"}).startswith("Opened")
    assert seen and seen[0].lower().endswith("pdf-dark.exe")


def test_program_in_read_only_folder_is_refused(monkeypatch, tmp_path):
    from core import fs_policy
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ")
    fs_policy.configure(folders=[{"path": str(tmp_path), "mode": "ro"}])
    try:
        monkeypatch.setattr(oa, "_open_file", _no_launch)
        out = oa.open_app({"app_name": str(exe)})
        assert out.startswith("[FOLDER_POLICY]") and "read-only" in out
    finally:
        fs_policy.reset()


def test_keyboard_fallback_never_claims_success(monkeypatch):
    monkeypatch.setitem(oa._OS_LAUNCHERS, oa._SYSTEM, lambda n: None)   # typed into search
    out = oa.open_app({"app_name": "UmAppQueNaoExiste"})
    assert not out.startswith("Opened") and "cannot confirm" in out
