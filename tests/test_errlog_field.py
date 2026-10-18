"""T020a — corrections found by the first real run of T021 (2026-10-17).

Every fixture below is modelled on what the real app wrote to logs/failures.jsonl:
the Wi-Fi drop that arrived as an ExceptionGroup, the offline Gemini ladder, the
`Access denied` text an action returned, and the QueueFull raised inside a loop callback.
The exception classes are local look-alikes: no google.genai, no websockets, no network.
"""
import asyncio
import json
import re
import socket
import types

import pytest

from core import errlog, gemini


# ── look-alikes of the real exceptions ───────────────────────────────────────
class APIError(Exception):                       # google.genai.errors.APIError
    pass


class ConnectionClosedError(Exception):          # websockets.exceptions.ConnectionClosedError
    pass


class ConnectError(Exception):                   # httpx.ConnectError
    pass


def wifi_drop_group():
    """The real shape: TaskGroup ExceptionGroup, sub 1 = APIError 1006 whose __context__ is a
    ConnectionClosedError; sub 2 = ConnectionClosedError caused by ConnectionResetError
    [WinError 64], itself from an OSError."""
    sub1 = APIError("1006 None. abnormal closure [internal]")
    sub1.__context__ = ConnectionClosedError("no close frame received or sent")
    try:
        try:
            raise OSError(64, "O nome da rede especificado não está mais disponível")
        except OSError as os_err:
            raise ConnectionResetError("[WinError 64] O nome da rede especificado não está mais disponível") from os_err
    except ConnectionResetError as reset:
        sub2 = ConnectionClosedError("no close frame received or sent")
        sub2.__cause__ = reset
    return ExceptionGroup("unhandled errors in a TaskGroup", [sub1, sub2])


def raised(exc):
    try:
        raise exc
    except BaseException as e:
        return e


# ── (1) classify_live_error takes the exception and unwraps it ───────────────
def test_wifi_drop_group_is_a_network_error():
    assert errlog.classify_live_error(raised(wifi_drop_group()), resumed=False) == "net"
    assert errlog.classify_live_error(raised(wifi_drop_group()), resumed=True) == "net"


def test_each_leaf_alone_is_enough_to_classify():
    sub1 = APIError("1006 None. abnormal closure [internal]")
    assert errlog.classify_live_error(sub1) == "net"
    reset = ConnectionResetError("[WinError 64] network name is no longer available")
    wrapped = ConnectionClosedError("no close frame received or sent")
    wrapped.__cause__ = reset
    assert errlog.classify_live_error(wrapped) == "net"


def test_cause_and_context_are_followed():
    inner = TimeoutError("timed out")
    try:
        try:
            raise inner
        except TimeoutError:
            raise RuntimeError("wrapper with nothing useful in it")       # implicit __context__
    except RuntimeError as e:
        assert errlog.classify_live_error(e) == "net"


def test_a_quota_leaf_inside_a_group_is_quota():
    group = BaseExceptionGroup("unhandled errors in a TaskGroup",
                           [asyncio.CancelledError(), APIError("429 RESOURCE_EXHAUSTED. quota")])
    assert errlog.classify_live_error(group) == "quota"


def test_string_input_still_works_and_unrelated_group_is_other():
    assert errlog.classify_live_error("getaddrinfo failed") == "net"
    assert errlog.classify_live_error(ExceptionGroup("g", [ValueError("nothing known")])) == "other"
    assert errlog.classify_live_error(None) == "other"


def test_circular_chains_do_not_hang():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__context__, b.__context__ = b, a
    assert errlog.classify_live_error(a) == "other"


# ── (2) a group is recorded by its first meaningful leaf ─────────────────────
@pytest.fixture
def on(log_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "unit-test-secret-value-0001"}), encoding="utf-8")
    monkeypatch.setattr(errlog, "_CONFIG_PATH", cfg)
    errlog.setup(log_dir, tee_console=False)
    return log_dir / "failures.jsonl"


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_group_failure_uses_the_leaf_for_type_message_and_signature(on):
    errlog.record_failure("live", "live_connect", exc=raised(wifi_drop_group()), phase="connect")
    errlog.record_failure("live", "live_connect", exc=raised(
        ExceptionGroup("unhandled errors in a TaskGroup", [ValueError("a different cause")])), phase="connect")
    errlog.flush()
    wifi, other = read_lines(on)
    assert wifi["error_type"] == "APIError"
    assert wifi["message"] == "1006 None. abnormal closure [internal]"
    assert "ExceptionGroup" in wifi["traceback"] and "ConnectionResetError" in wifi["traceback"]   # nothing lost
    assert other["error_type"] == "ValueError"
    assert wifi["signature"] != other["signature"]               # different causes never share a group
    assert wifi["origin"].startswith("tests/test_errlog_field.py:")


def test_cancelled_leaves_are_skipped_unless_they_are_all_there_is(on):
    errlog.record_failure("live", "x", exc=raised(BaseExceptionGroup(
        "g", [asyncio.CancelledError(), ConnectError("boom")])))
    errlog.record_failure("live", "y", exc=raised(BaseExceptionGroup("g", [asyncio.CancelledError()])))
    errlog.flush()
    first, second = read_lines(on)
    assert first["error_type"] == "ConnectError"
    assert second["error_type"] == "CancelledError"


def test_nested_groups_are_flattened(on):
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [KeyError("k")])])
    errlog.record_failure("live", "x", exc=raised(nested))
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["error_type"] == "KeyError"


def test_plain_exceptions_are_unchanged(on):
    errlog.record_failure("tool", "t", exc=raised(ValueError("plain")))
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["error_type"] == "ValueError" and rec["message"] == "plain"


# ── (3) network failures on the Gemini ladder ────────────────────────────────
@pytest.mark.parametrize("exc", [
    ConnectError("[Errno 11001] getaddrinfo failed"),
    socket.gaierror(11001, "getaddrinfo failed"),
    ConnectionRefusedError("refused"),
    ConnectionError("lost"),
    OSError("[Errno 101] Network is unreachable"),
])
def test_rung_failure_reason_network(exc):
    assert errlog.rung_failure_reason(exc, str(exc)) == "network"


def test_network_error_wrapped_by_another_is_still_network():
    try:
        try:
            raise socket.gaierror(11001, "getaddrinfo failed")
        except socket.gaierror as g:
            raise RuntimeError("request failed") from g
    except RuntimeError as e:
        assert errlog.rung_failure_reason(e, str(e)) == "network"


@pytest.mark.parametrize("exc, expected", [
    (asyncio.TimeoutError(), "timeout"),
    (RuntimeError("the read operation timed out"), "timeout"),
    (ValueError("something else"), "error"),
])
def test_rung_failure_reason_other(exc, expected):
    assert errlog.rung_failure_reason(exc, str(exc)) == expected


def test_ladder_message_when_nothing_is_reachable():
    reasons = {"a": "network", "b": "network", "c": "network"}
    assert errlog.ladder_message(reasons) == "SYS: Gemini unreachable — check the connection."


def test_ladder_message_mixed_network_is_not_unreachable():
    assert "unreachable" not in errlog.ladder_message({"a": "network", "b": "timeout"})


def test_offline_ladder_is_recorded_as_network_and_announced(on, tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "api_key", lambda refresh=False: "AIza" + "x" * 35)
    gemini._cooldown.clear()
    seen = []
    errlog.bind(notify=seen.append)

    def offline(*a, **k):
        raise ConnectError("[Errno 11001] getaddrinfo failed")

    def offline_live(*a, **k):
        raise socket.gaierror(11001, "getaddrinfo failed")
    monkeypatch.setattr(gemini, "client", offline)
    monkeypatch.setattr(gemini, "_live_call", offline_live)
    try:
        assert gemini.call("x", tier=gemini.FAST) is None
        errlog.flush()
        recs = read_lines(on)
        assert {r["error_type"] for r in recs if r["name"] != "ladder"} == {"network"}
        assert seen == ["SYS: Gemini unreachable — check the connection."]
        (end,) = [r for r in recs if r["name"] == "ladder"]
        assert set(end["context"]["reasons"].values()) == {"network"}
    finally:
        gemini._cooldown.clear()


# ── (4) soft failures: the strings real actions return ───────────────────────
REAL_FAILURES = [
    ("Access denied: C:\\não\\existe.txt", "soft:access denied"),                  # file_controller
    ("Access denied (source): C:\\a.txt", "soft:access denied"),
    ("Permission denied: C:\\Windows\\x", "soft:permission denied"),
    ("File not found: C:\\x\\y.docx", "soft:file not found"),
    ("Path not found: D:\\none", "soft:path not found"),
    ("Source not found: a.txt", "soft:source not found"),
    ("Image not found: shot.png", "soft:image not found"),
    ("Command not found: ffprobe", "soft:command not found"),
    ("Element not found on screen: Install", "soft:element not found"),
    ("Element not found (timeout).", "soft:element not found"),
    ("ffmpeg not found. Install ffmpeg to extract audio", "soft:ffmpeg not found"),
    ("Convert failed: bad codec", "soft:convert failed"),
    ("Trim failed: x", "soft:trim failed"),
    ("AI processing failed: 429", "soft:ai processing failed"),
    ("Scheduling failed: no task scheduler", "soft:scheduling failed"),
    ("Epic launch failed: x", "soft:epic launch failed"),
    ("Summary generation failed, sir: quota", "soft:summary generation failed"),
    ("Flight search failed, sir: HTTP 500", "soft:flight search failed"),
    ("focus_window (Windows) failed: x", "soft:focus window failed"),
    ("Click error: no window", "soft:click error"),
    ("Scroll error: x", "soft:scroll error"),
    ("Key error: x", "soft:key error"),
    ("Execution error: boom", "soft:execution error"),
    ("File controller error (copy): x", "soft:file controller error"),
    ("Desktop control error: x", "soft:desktop control error"),
    ("Search error: x", "soft:search error"),
    ("Failed to open notepad.exe: x", "soft:failed to open"),
    ("Could not read file: x.txt", "soft:could not read"),
    ("Could not start Steam.", "soft:could not start"),
    ("Unable to reach the printer", "soft:unable to reach"),
    ("Error listing files: x", "soft:error listing files"),
    ("ERROR: every Gemini model on the ladder failed", "soft:error"),
    ("Cannot open that file type", "soft:cannot open that"),
    ("Can't reach the server", "soft:can't reach the"),
    ("No such file or directory: x", "soft:no such file"),
    ("pandas not installed. Run: pip install pandas", "soft:pandas not installed"),
    ("pydub not installed.", "soft:pydub not installed"),
    ("youtube-transcript-api is not installed.", "soft:youtube transcript api"),
    ("PyAutoGUI is not installed — cannot control the mouse", "soft:pyautogui is not"),
    ("pyperclip not available", "soft:pyperclip not available"),
    ("Unsupported file type: .xyz", "soft:unsupported file type"),
    ("Unknown PDF action: spin", "soft:unknown pdf action"),
    ("Invalid JSON: Expecting value", "soft:invalid json"),
    ("Timed out after 30s.", "soft:timed out after"),
    ("Execution timed out (30s).", "soft:execution timed out"),
    ("Direct execution not supported for .dll files", "soft:direct execution not"),
    ("Sir, I couldn't find that application.", "soft:i couldn't find"),
    ("I couldn't read the clipboard", "soft:i couldn't read"),
    ("Something went wrong while scheduling the reminder", "soft:something went wrong"),
    ("Not a file: C:/x", "soft:not a file"),
    ("Path is not a file: C:/x", "soft:path is not"),
    ("Not a directory: C:/x", "soft:not a directory"),
    ("Protected directory, cannot delete: C:\\Windows", "soft:protected directory cannot"),
]


@pytest.mark.parametrize("text, expected", REAL_FAILURES)
def test_real_action_failures_are_soft_failures(text, expected):
    assert errlog.classify_soft_failure(text) == expected


LEGITIMATE = [
    "Done.", "Opened Excel", "Opened: https://example.com", "Opened in Chrome: x",
    "No results found for: cats", "No news found for: x", "No files found.", "Desktop is empty.",
    "Directory is empty: C:\\x", "Not found in monitored topics: bitcoin",
    "Found 3 file(s):\n a\n b", "File created: C:\\x\\a.txt", "Saved to: C:\\x", "Clicked: Install",
    "Typed: hello", "Waited 3s", "Volume set to 40%.", "Valid JSON. Type: dict, size: 3 B",
    "Message sent to Ana via WhatsApp.", "Page reloaded: https://x.test",
    "Converted to CSV. Saved: C:\\x.csv", "Screenshot saved: C:\\x.png", "Nothing is playing.",
    "Stopped monitoring: x", "Wallpaper set: a.jpg", "Daily game update scheduled at 3:00.",
    "unknown", "No active tab to close.", "Not found in monitored topics: bitcoin", "Please specify a topic to monitor.",
    "Tool 'x' failed: boom", "Sir, the 'x' plugin failed: boom",
    "The word error: appears here but this sentence is plainly an ordinary and long result text",
]


@pytest.mark.parametrize("text", LEGITIMATE)
def test_legitimate_results_are_not_failures(text):
    assert errlog.classify_soft_failure(text) is None


# ── (5) asyncio exception handler ────────────────────────────────────────────
class FakeLoop:
    def __init__(self):
        self.default_calls = []

    def default_exception_handler(self, context):
        self.default_calls.append(context)


def test_asyncio_handler_records_and_chains_to_the_default(on):
    loop = FakeLoop()
    ctx = {"message": "Exception in callback X", "exception": asyncio.QueueFull()}
    errlog.asyncio_exception_handler(loop, ctx)
    errlog.flush()
    assert loop.default_calls == [ctx]                         # the console still gets its traceback
    (rec,) = read_lines(on)
    assert rec["kind"] == "uncaught" and rec["component"] == "app" and rec["name"] == "asyncio"
    assert rec["error_type"] == "QueueFull"
    assert "Exception in callback X" in rec["context"]["message"]
    assert re.fullmatch(r"[0-9a-f]{12}", rec["signature"])


def test_asyncio_handler_without_exception_object(on):
    loop = FakeLoop()
    errlog.asyncio_exception_handler(loop, {"message": "Task was destroyed but it is pending!"})
    errlog.flush()
    (rec,) = read_lines(on)
    assert rec["kind"] == "uncaught" and "Task was destroyed" in rec["message"]
    assert len(loop.default_calls) == 1


def test_asyncio_handler_never_raises_and_still_chains(on):
    loop = FakeLoop()
    errlog.asyncio_exception_handler(loop, None)               # garbage context
    errlog.asyncio_exception_handler(loop, {"exception": object()})
    assert len(loop.default_calls) == 2


def test_asyncio_handler_on_a_real_loop(on):
    loop = asyncio.new_event_loop()
    seen = []
    try:
        loop.set_exception_handler(
            lambda lp, ctx: (errlog.asyncio_exception_handler(types.SimpleNamespace(
                default_exception_handler=lambda c: seen.append(c)), ctx)))

        async def main():
            loop.call_soon(lambda: (_ for _ in ()).throw(asyncio.QueueFull()))
            await asyncio.sleep(0.05)
        loop.run_until_complete(main())
    finally:
        loop.close()
    errlog.flush()
    assert len(seen) == 1
    (rec,) = read_lines(on)
    assert rec["error_type"] == "QueueFull"
