"""An action's speak() must never reach the Live model as the user talking.

On 2026-10-23 flight_finder's English progress line, sent as a bare user turn, got an
English reply and 8 more calls of the same tool. main.py is not imported here (Qt,
google.genai): its speak()/speak_error() are checked in the source with ast.
"""
import ast
from pathlib import Path

from core import tool_progress

ROOT = Path(__file__).resolve().parent.parent


def _method(name: str) -> ast.FunctionDef:
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JarvisLive":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"JarvisLive.{name} not found")


def test_frame_tags_the_line_and_keeps_the_text():
    msg = tool_progress.frame("Fetching the transcript now, sir.")
    assert msg.startswith("[TOOL_PROGRESS] ")
    assert msg.endswith("Fetching the transcript now, sir.")
    assert "not the user" in msg and "call no tool" in msg
    assert "user's language" in msg


def test_frame_flattens_newlines_and_survives_none():
    assert "\n" not in tool_progress.frame("a\nb\n\nc")
    assert tool_progress.frame("a\nb").endswith("a b")
    assert tool_progress.frame(None).startswith("[TOOL_PROGRESS]")


def test_prompt_documents_the_tag():
    prompt = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
    tagged = prompt.split("[TAGGED MESSAGES]", 1)[1].split("[SPEED]", 1)[0]
    assert "[TOOL_PROGRESS]" in tagged
    section = tagged[tagged.index("[TOOL_PROGRESS]"):].replace("\r\n", " ").replace("\n", " ")
    assert "not the user" in section and "call no tool" in section


def test_lines_go_to_the_model_only_with_no_tool_call_open():
    # Real session 2026-10-26: a tagged line during an open call repeated the call.
    assert tool_progress.should_send(0) is True
    assert tool_progress.should_send(1) is False
    assert tool_progress.should_send(3) is False


def test_speak_sends_only_framed_text():
    src = ast.unparse(_method("speak"))
    assert "send_client_content" in src
    assert "tool_progress.frame(text)" in src
    # The open-call gate comes before the send.
    assert "tool_progress.should_send(len(self._tool_tasks))" in src
    assert src.index("should_send") < src.index("send_client_content")
    # The raw text never goes into a part on its own.
    assert "{'text': text}" not in src and '{"text": text}' not in src


def test_speak_error_sends_nothing_to_the_model():
    src = ast.unparse(_method("speak_error"))
    assert "send_client_content" not in src
    assert "self.speak(" not in src
    assert "write_log" in src
