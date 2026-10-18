"""
Secret filter for everything JARVIS writes to logs/ (spec FR-005, research R4).

Pure functions, standard library only. The errlog writer thread runs every record
through ``redact`` right before it reaches disk, so a key that an action put into a
traceback, a tool argument or a printed URL never survives as more than a
recognisable stub: ``‹redacted:AIza…f3›`` (4 first + 2 last characters, enough to tell
*which* key leaked without revealing it).

Three layers, applied in this order:
  1. literal values — every string of config/api_keys.json with 16+ characters
     (the Gemini key, plugin credentials), supplied by the caller;
  2. known shapes — Google ``AIza…``, ``sk-…`` (OpenAI / DeepSeek / Anthropic),
     Telegram bot tokens, ``Bearer …``, ``Authorization: …``, ``?key=`` style URL params;
  3. dict keys by name — a *string* value under a key named key/api_key/apiKey (final or
     isolated word) or containing token/secret/password/authorization/cookie is masked whole. Numbers and containers are left
     alone: the usage records have a ``tokens`` object that must stay readable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

# Below this a value is too short to show a prefix/suffix without revealing it.
_MIN_STUB = 12
# A "secret" shorter than this (an empty string, "o") would mangle every log line.
_MIN_SECRET = 6

# "key" is sensitive only as a final or isolated word (key, api_key, apiKey,
# gemini_api_key, x-api-key, APIKEY). A plain substring match would also swallow
# `keys: "ctrl+c"` from the keyboard action, `hotkey` and `monkey`, and triage needs
# those readable. token/secret/password/authorization/cookie match anywhere.
_SENSITIVE_KEY = re.compile(
    r"(?i:token|secret|passw(?:or)?d|authorization|cookie)"
    r"|(?i:(?:^|[^a-z0-9])key$)"          # key / api_key / x-api-key
    r"|[a-z0-9]Key$"                      # apiKey (camelCase)
    r"|(?i:api[_\-]?key)"                # apikey / APIKEY
)

# Compiled once at import: the writer thread calls redact for every record and every
# console line.
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)")
_BEARER = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=\-]{8,})")
_URL_PARAM = re.compile(r"(?i)([?&](?:api_?key|key|access_token|token)=)([^&\s\"'<>]+)")
_SHAPES = (
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),                 # Google API key
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),                 # OpenAI / DeepSeek / sk-proj- / sk-ant-
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b"),        # Telegram bot token
)


def mask(secret: str) -> str:
    """``‹redacted:AIza…f3›`` — or a bare ``‹redacted›`` when the value is too short to hint at."""
    s = str(secret)
    if len(s) < _MIN_STUB:
        return "‹redacted›"
    return f"‹redacted:{s[:4]}…{s[-2:]}›"


def _redact_str(text: str, secrets: list[str]) -> str:
    for s in secrets:                                       # longest first, see redact()
        if s in text:
            text = text.replace(s, mask(s))
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + mask(m.group(2).strip()), text)
    text = _BEARER.sub(lambda m: m.group(1) + mask(m.group(2)), text)
    text = _URL_PARAM.sub(lambda m: m.group(1) + mask(m.group(2)), text)
    for rx in _SHAPES:
        text = rx.sub(lambda m: mask(m.group(0)), text)
    return text


def _walk(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return _redact_str(value, secrets)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, str) and isinstance(k, str) and _SENSITIVE_KEY.search(k):
                out[k] = mask(v)
            else:
                out[k] = _walk(v, secrets)
        return out
    if isinstance(value, list):
        return [_walk(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(_walk(v, secrets) for v in value)
    return value


def redact(value: Any, secrets: Iterable[str] = ()) -> Any:
    """Return a masked copy of ``value`` (str / dict / list / tuple, nested). The input is never mutated."""
    ordered = sorted({s for s in secrets if isinstance(s, str) and len(s) >= _MIN_SECRET},
                     key=len, reverse=True)                 # longest first: a key that contains another
    return _walk(value, ordered)


def _collect_strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        if len(node) >= 16 and node not in out:
            out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_strings(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _collect_strings(v, out)


def load_config_secrets(path: Path) -> list[str]:
    """String values with 16+ characters from a JSON config (api_keys.json). Never raises."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    found: list[str] = []
    _collect_strings(data, found)
    return found
