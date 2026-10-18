"""Failure hooks in core/action_loader.py and core/plugin_loader.py (spec FR-003).

The loaders already isolate a crashing action/plugin and turn it into a text for the
model; the hook only adds a record. Nothing the user sees may change.
"""
import json
import sys
import textwrap

import pytest

from core import action_loader, errlog, plugin_loader

SECRET = "unit-test-secret-value-0001"


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _boom_handler(parameters=None, **_):
    """Lives in the project's tests/ folder so the failure's origin is a project file."""
    raise ValueError(f"handler exploded with {SECRET}")


@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"k": SECRET}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    yield log_dir / "failures.jsonl"
    for name in [n for n in sys.modules if n.startswith(("actions.hooktest_", "plugins.hooktest_"))]:
        sys.modules.pop(name, None)


def _write(path, src):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(src), encoding="utf-8")


ACTION_OK_DECL = """
    from tests.test_loader_hooks import _boom_handler
    TOOL = {{"name": "{name}", "description": "x", "parameters": {{"type": "OBJECT", "properties": {{}}}},
             "handler": _boom_handler}}
"""


def test_action_run_failure_is_recorded_and_text_is_unchanged(on, tmp_path):
    adir = tmp_path / "actions"
    _write(adir / "hooktest_boom.py", ACTION_OK_DECL.format(name="hooktest_boom"))
    logged = []
    reg = action_loader.discover_actions(adir, logger=logged.append)
    out = reg.run("hooktest_boom", {"path": "a.txt", "api_key": SECRET}, {})
    errlog.flush()
    assert out == f"Tool 'hooktest_boom' failed: handler exploded with {SECRET}"    # untouched
    assert any("crashed during run()" in line for line in logged)
    (rec,) = [r for r in read_lines(on) if r["phase"] == "run"]
    assert rec["kind"] == "exception" and rec["component"] == "action" and rec["name"] == "hooktest_boom"
    assert rec["error_type"] == "ValueError"
    assert rec["origin"] == "tests/test_loader_hooks.py:_boom_handler"
    assert rec["context"]["args"]["path"] == "a.txt"
    assert rec["context"]["args"]["api_key"].startswith("‹redacted")
    assert SECRET not in json.dumps(rec)
    assert "ValueError" in rec["traceback"]


def test_action_load_failure_is_recorded(on, tmp_path):
    adir = tmp_path / "actions"
    _write(adir / "hooktest_badimport.py", """
        from tests.test_loader_hooks import _boom_handler
        _boom_handler({})
    """)
    logged = []
    reg = action_loader.discover_actions(adir, logger=logged.append)
    errlog.flush()
    assert not reg.has("hooktest_badimport")
    assert any(line.startswith("Action rejected: hooktest_badimport.py") for line in logged)
    (rec,) = read_lines(on)
    assert rec["component"] == "action" and rec["name"] == "hooktest_badimport" and rec["phase"] == "load"
    assert rec["origin"] == "tests/test_loader_hooks.py:_boom_handler"


def test_action_that_works_records_nothing(on, tmp_path):
    adir = tmp_path / "actions"
    _write(adir / "hooktest_fine.py", """
        TOOL = {"name": "hooktest_fine", "description": "x",
                "parameters": {"type": "OBJECT", "properties": {}}, "handler": lambda parameters: "fine"}
    """)
    reg = action_loader.discover_actions(adir, logger=lambda _m: None)
    assert reg.run("hooktest_fine", {}, {}) == "fine"
    errlog.flush()
    assert not on.exists()


PLUGIN_SRC = """
    from tests.test_loader_hooks import _boom_handler
    PLUGIN = {{"name": "{name}", "description": "x", "parameters": {{"type": "OBJECT", "properties": {{}}}}}}
    def run(parameters):
        return _boom_handler(parameters)
"""


def test_plugin_run_failure_is_recorded_and_text_is_unchanged(on, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "get_plugin_enabled", lambda name: True)
    pdir = tmp_path / "plugins"
    _write(pdir / "hooktest_pboom.py", PLUGIN_SRC.format(name="hooktest_pboom"))
    notices = []
    reg = plugin_loader.discover_plugins(pdir, set(), logger=lambda _m: None, notify=notices.append)
    out = reg.run("hooktest_pboom", {"q": "hello", "token": SECRET})
    errlog.flush()
    assert out == f"Sir, the 'hooktest_pboom' plugin failed: handler exploded with {SECRET}"
    assert notices == ["Plugin 'hooktest_pboom' failed — see the console for details."]
    (rec,) = [r for r in read_lines(on) if r["phase"] == "run"]
    assert rec["component"] == "plugin" and rec["name"] == "hooktest_pboom" and rec["kind"] == "exception"
    assert rec["origin"] == "tests/test_loader_hooks.py:_boom_handler"
    assert rec["context"]["args"]["q"] == "hello"
    assert SECRET not in json.dumps(rec)


def test_plugin_load_failure_is_recorded_with_the_loader_message(on, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_loader, "get_plugin_enabled", lambda name: True)
    pdir = tmp_path / "plugins"
    _write(pdir / "hooktest_pbad.py", """
        import a_package_that_is_not_installed_anywhere
    """)
    reg = plugin_loader.discover_plugins(pdir, set(), logger=lambda _m: None)
    errlog.flush()
    assert not reg.has("hooktest_pbad")
    (rec,) = read_lines(on)
    assert rec["component"] == "plugin" and rec["name"] == "hooktest_pbad" and rec["phase"] == "load"
    assert rec["error_type"] == "ModuleNotFoundError"
    assert "pip install a_package_that_is_not_installed_anywhere" in rec["message"]
    assert rec["origin"].startswith("core/plugin_loader.py:")          # raised in the plugin, outside the project
