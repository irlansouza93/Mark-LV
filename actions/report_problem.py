"""
report_problem — the user says "that did not work" and JARVIS writes it down.

What lands in logs/reports.jsonl is the report in the user's words plus the context that makes
it fixable later: the last few lines of the conversation, the last tools that ran, and the
failures of the last ten minutes. ``lv-triage`` reads these first, because a person saying
what they expected is worth more than any traceback (spec 001, US3).

Risk class (constitution IV): READ — it only writes the app's own local log; nothing outside
logs/ is touched and nothing is irreversible.

Why a bundled action and not a plugin (research R13): a plugin can be switched off in the
plugins panel, and a way to report problems must not be something you can disable by accident.
A new file under actions/ is auto-discovered and does not touch any upstream file.

Blocking on purpose and instant (< 5 ms; measured well under 1 ms): the handler copies a few
small lists and queues one dict. The failures lookup, the secret filter and the disk all
happen on the log writer thread (core/errlog.py).

Declaration cost (constitution VI): see "Custo de tokens" in specs/001-error-log-llm-usage/plan.md.
Called only when the user asks (the system prompt is deliberately not changed).
"""
from core import errlog


def report_problem(parameters: dict) -> str:
    params = parameters or {}
    try:
        reason = errlog.record_report(params.get("expected"), params.get("happened"))
    except Exception as e:                     # record_report never raises; this is belt and braces
        reason = str(e) or type(e).__name__
    if reason:
        return f"Could not log it: {reason}"
    return "Logged. Confirm in one short sentence."


TOOL = {
    "name": "report_problem",
    "description": "Log a problem the user reports, e.g. 'that didn't work'. "
                   "If unclear, ask what they expected first.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "expected": {"type": "STRING", "description": "What the user wanted, in their words"},
            "happened": {"type": "STRING", "description": "What went wrong"},
        },
        "required": ["expected"],
    },
    "handler": report_problem,
}
