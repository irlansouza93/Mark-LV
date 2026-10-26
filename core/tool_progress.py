"""Frames what a running action wants said (``speak`` in an action's context) so the
Live model does not take it for the user.

``JarvisLive.speak`` can only reach the model through ``send_client_content``, and
that channel has one role for injected text: ``user``. Sent bare, an action's
"Searching flights..., sir" WAS the user talking: on 2026-10-23 the model answered
it in English and called ``flight_finder`` again, 8 times in a row. So every line
goes out behind the ``[TOOL_PROGRESS]`` tag, described under [TAGGED MESSAGES] in
``core/prompt.txt`` (not the user, say it briefly in the user's language, call no
tool), plus a one-line reminder inline - the tag alone relies on the prompt, which
the model weighs less after a long session.

The tag is not enough while the model is still waiting for a tool response: in a
real session (2026-10-26, gemini-3.1-flash-live-preview) the tagged line sent
during an open flight_finder call got that call repeated 1.2 s later and no speech.
So a line is only sent when no tool call is open (``should_send``); otherwise it
goes to the activity log, and the tool's result carries the outcome.

A tool that raised is not spoken through here at all: its tool response already
carries "Tool 'x' failed: ...", so a second message would only be a second turn.

Pure: no session, no Qt, so the tests build the exact text the model receives.
"""

TAG = "[TOOL_PROGRESS]"


def should_send(open_tool_calls: int) -> bool:
    """True when a progress line may become a model turn: no tool call is open."""
    return open_tool_calls <= 0


def frame(text: str) -> str:
    """The message the model receives for an action's progress line."""
    text = " ".join(str(text or "").split())
    return (f"{TAG} Status from a job a tool left running (the app, not the user; say it "
            f"briefly in the user's language, call no tool): {text}")
