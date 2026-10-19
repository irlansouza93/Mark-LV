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


def test_existing_path_still_goes_to_launcher(monkeypatch, tmp_path):
    f = tmp_path / "exists.txt"
    f.write_text("x", encoding="utf-8")
    seen = []
    monkeypatch.setitem(oa._OS_LAUNCHERS, oa._SYSTEM, lambda n: seen.append(n) or True)
    assert oa.open_app({"app_name": str(f)}).startswith("Opened")
    assert seen


def test_app_names_and_uris_are_not_paths():
    for name in ("WhatsApp", "Chrome", "libreoffice --writer", "ms-settings:",
                 "https://example.com/a/b", "notepad.exe"):
        assert oa._file_path_of(name) is None, name
