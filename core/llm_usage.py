"""
LLM usage and cost records (spec 001, US2).

Every call the assistant makes to a model leaves one line in ``logs/usage-YYYY-MM.jsonl``:
the one-shot calls that walk the Gemini ladder (``kind: "oneshot"``), each turn of the
voice conversation (``live_turn``), the voice-picker samples (``tts``) and the start/end
of each voice session (``live_session``). A line says which model answered, how many
tokens went in and out (text / audio / image apart), how long it took, whether it
worked, and what the call WOULD cost on the paid tier — also while the key is on the
free tier, because that is the number the free-vs-paid decision (D10/D12) needs.

Where the work happens
    The callers are the audio receive loop and plugin threads, so the public functions
    here only read a few attributes and put a dict on the errlog queue (~5 microseconds,
    measured in tests/test_llm_usage.py). Token extraction, the price lookup, billing
    and the per-session bookkeeping run in the errlog WRITER thread, through the
    ``_finalize`` hook of ``errlog._enqueue`` — which also means the Live turn meter is
    only ever touched by one thread, in message order, with no lock.

Prices live in ``config/llm_prices.json`` (editable, dated, cited). The cost is computed
when the call happens and stored: editing the table later never rewrites history.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path

from core import errlog
from memory.config_manager import get_llm_billing

ROOT = Path(__file__).resolve().parent.parent
# Read at call time (not bound as a default) so tests can point it at a throwaway table.
PRICES_PATH = ROOT / "config" / "llm_prices.json"

_UTC = timezone.utc
TOKEN_FIELDS = ("in_text", "in_audio", "in_image", "in_cached", "out_text", "out_audio", "thoughts", "total")
_RESULTS = ("ok", "quota", "unavailable", "gone", "timeout", "error")
_CALL_KINDS = ("oneshot", "live_turn", "tts")


# ═════════════════════════════════════════════════════════════════════════════
#  Pure: tokens, prices, cost
# ═════════════════════════════════════════════════════════════════════════════
def _get(obj, name):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _count(v):
    """A token count: a non-negative int, else None (unknown — never silently 0)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, float) and v == v and v >= 0:
        return int(v)
    return None


def _modality(entry) -> str:
    m = _get(entry, "modality")
    name = getattr(m, "name", None) or str(m or "TEXT")
    name = name.upper().rsplit(".", 1)[-1]
    if name == "AUDIO":
        return "audio"
    if name in ("IMAGE", "VIDEO"):
        return "image"
    return "text"


def _by_modality(details) -> dict:
    """``[{modality, token_count}, …]`` -> ``{"text": n, "audio": n, "image": n}`` (only those present)."""
    out: dict = {}
    if not isinstance(details, (list, tuple)):
        return out
    for e in details:
        n = _count(_get(e, "token_count"))
        if n is not None:
            k = _modality(e)
            out[k] = out.get(k, 0) + n
    return out


def tokens_from_usage(usage_metadata) -> dict | None:
    """SDK ``usage_metadata`` (REST ``GenerateContentResponseUsageMetadata`` or Live
    ``UsageMetadata``, object or dict) -> the ``tokens`` field of a usage record, or None
    when it carries no counts at all.

    A field the provider did not report stays None, not 0. Without per-modality details
    everything counts as text. ``thoughts`` are reported apart because they are billed as
    output. ``total`` is the provider's when given, else the sum of what is known."""
    if usage_metadata is None:
        return None
    try:
        t = dict.fromkeys(TOKEN_FIELDS)
        din = _by_modality(_get(usage_metadata, "prompt_tokens_details"))
        prompt = _count(_get(usage_metadata, "prompt_token_count"))
        if din:
            for k, v in din.items():
                t[f"in_{k}"] = v
        elif prompt is not None:
            t["in_text"] = prompt
        dout = _by_modality(_get(usage_metadata, "candidates_tokens_details")
                            or _get(usage_metadata, "response_tokens_details"))
        out = _count(_get(usage_metadata, "candidates_token_count"))
        if out is None:
            out = _count(_get(usage_metadata, "response_token_count"))
        if dout:
            t["out_text"] = dout.get("text")
            t["out_audio"] = dout.get("audio")
        elif out is not None:
            t["out_text"] = out
        t["in_cached"] = _count(_get(usage_metadata, "cached_content_token_count"))
        t["thoughts"] = _count(_get(usage_metadata, "thoughts_token_count"))
        t["total"] = _count(_get(usage_metadata, "total_token_count"))
        if all(v is None for v in t.values()):
            return None
        if t["total"] is None:
            known = [t[k] for k in ("in_text", "in_audio", "in_image", "out_text", "out_audio", "thoughts")
                     if t[k] is not None]
            t["total"] = sum(known) if known else None
        return t
    except Exception:
        return None


def _valid_price(p) -> bool:
    if isinstance(p, bool):
        return False
    if isinstance(p, (int, float)):
        return p >= 0
    if isinstance(p, dict):
        return bool(p) and all(isinstance(k, str) and _valid_price(v) and not isinstance(v, dict)
                               for k, v in p.items())
    return False


def _validate_entry(key, entry):
    """Why an entry is unusable, or None when it is fine."""
    if not isinstance(key, str) or not re.match(r"^[A-Za-z0-9_.-]+/.+$", key):
        return "key must look like provider/model"
    if not isinstance(entry, dict):
        return "entry is not an object"
    if not _valid_price(entry.get("in")):
        return "'in' is missing or not a non-negative price"
    if not _valid_price(entry.get("out")):
        return "'out' is missing or not a non-negative price"
    if "in_cached" in entry and not (_valid_price(entry["in_cached"]) and not isinstance(entry["in_cached"], dict)):
        return "'in_cached' is not a non-negative number"
    if "peak_multiplier" in entry:
        pm = entry["peak_multiplier"]
        if isinstance(pm, bool) or not isinstance(pm, (int, float)) or pm <= 0:
            return "'peak_multiplier' must be a positive number"
    for step in entry.get("schedule", []) or []:
        try:
            date.fromisoformat(step["from"])
        except Exception:
            return "'schedule' items need a 'from' date (YYYY-MM-DD)"
        for k in ("in", "out", "in_cached"):
            if k in step and not _valid_price(step[k]):
                return f"'schedule' item has an invalid '{k}'"
    return None


_prices_cache: dict = {}


def _empty_table() -> dict:
    return {"v": 1, "checked": None, "source": [], "unit": "usd_per_1m_tokens", "models": {}}


def load_prices(path: Path | None = None) -> dict:
    """The price table (validated), cached until the file's mtime changes. An entry with a
    negative or non-numeric price is skipped with a console line naming it — never an
    exception. A missing or unreadable file is an empty table (every model then has an
    unknown cost, which the report lists)."""
    path = Path(path) if path is not None else Path(PRICES_PATH)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return _empty_table()
    cached = _prices_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    table = _empty_table()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        models = raw.get("models") if isinstance(raw, dict) else None
        if isinstance(models, dict):
            for key, entry in models.items():
                why = _validate_entry(key, entry)
                if why:
                    print(f"[usage] price table: skipped {key!r}: {why}")
                else:
                    table["models"][key] = entry
            for k in ("v", "checked", "source", "unit"):
                if k in raw:
                    table[k] = raw[k]
    except Exception as e:
        print(f"[usage] price table unreadable ({type(e).__name__}: {e}); every cost will be unknown")
    _prices_cache[path] = (mtime, table)
    return table


def _norm_model(model) -> str:
    s = str(model or "")
    return s[7:] if s.startswith("models/") else s


# DeepSeek charges double 01-04h and 06-10h UTC (docs/llm-gestao.md section 2).
_PEAK_UTC_HOURS = ((1, 4), (6, 10))


def _in_peak(when: datetime) -> bool:
    h = (when.astimezone(_UTC) if when.tzinfo else when).hour
    return any(a <= h < b for a, b in _PEAK_UTC_HOURS)


def _effective(entry: dict, when) -> dict:
    """Base prices, replaced by the latest ``schedule`` step already in force on ``when``."""
    eff = {k: entry[k] for k in ("in", "out", "in_cached") if k in entry}
    if when is not None and entry.get("schedule"):
        day = (when.astimezone(_UTC) if when.tzinfo else when).date()
        for step in sorted(entry["schedule"], key=lambda s: s["from"]):
            if day >= date.fromisoformat(step["from"]):
                eff.update({k: step[k] for k in ("in", "out", "in_cached") if k in step})
    return eff


def _unit(price, modality):
    if isinstance(price, dict):
        v = price.get(modality)
        return float(price["text"] if v is None and "text" in price else (v or 0.0))
    return float(price or 0.0)


def cost(prices: dict, provider: str, model: str, tokens: dict | None,
         when: datetime | None = None) -> tuple[float | None, bool]:
    """``(usd, price_missing)`` for one call, by the table.

    * model not in the table            -> ``(None, True)``
    * no token counts                   -> ``(None, False)`` (the model is priced, the usage is not known)
    * per-modality input/output prices where the provider has them (Live: audio costs more);
      a modality without its own price uses the text price
    * ``thoughts`` are billed as text output; ``in_cached`` at the cached price when the table
      has one (it is a subset of ``in_text``), otherwise at the normal input price
    * ``when`` selects the price ``schedule`` step and the DeepSeek peak multiplier.
    Never raises."""
    try:
        models = prices.get("models") if isinstance(prices, dict) else None
        entry = models.get(f"{provider}/{_norm_model(model)}") if isinstance(models, dict) else None
        if not isinstance(entry, dict) or "in" not in entry or "out" not in entry:
            return None, True
        if tokens is None:
            return None, False
        eff = _effective(entry, when)
        g = lambda k: tokens.get(k) or 0                       # noqa: E731 — unknown counts as nothing to bill
        in_text = g("in_text")
        total = 0.0
        if "in_cached" in eff and g("in_cached"):
            cached = min(g("in_cached"), in_text)
            in_text -= cached
            total += cached * float(eff["in_cached"])
        total += in_text * _unit(eff["in"], "text")
        total += g("in_audio") * _unit(eff["in"], "audio")
        total += g("in_image") * _unit(eff["in"], "image")
        total += g("out_text") * _unit(eff["out"], "text")
        total += g("out_audio") * _unit(eff["out"], "audio")
        total += g("thoughts") * _unit(eff["out"], "text")
        usd = total / 1_000_000
        if when is not None and entry.get("peak_multiplier") and _in_peak(when):
            usd *= float(entry["peak_multiplier"])
        return round(usd, 10), False
    except Exception:
        return None, True


def result_of(err) -> str:
    """Map a failure (exception or text) or one of our own reason words to the ``result``
    enum of a usage record: ok | quota | unavailable | gone | timeout | error."""
    if isinstance(err, str) and err in _RESULTS:
        return err
    if err == "network":
        return "error"
    text = str(err or "")
    low = text.lower()
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "quota"
    if "404" in text or "403" in text or "not found" in low or "permission" in low or "is not supported" in low:
        return "gone"
    if isinstance(err, (TimeoutError,)) or "timed out" in low:
        return "timeout"
    if "503" in text or "504" in text or "unavailable" in low or "deadline_exceeded" in low:
        return "unavailable"
    return "error"


# ═════════════════════════════════════════════════════════════════════════════
#  The Live turn meter — THE one place that decides what a turn's tokens are
# ═════════════════════════════════════════════════════════════════════════════
class LiveUsageMeter:
    """Turns the ``usage_metadata`` messages of a Live session into one ``tokens`` dict per turn.

    THE RULE (research R7, measured 2026-10-17 with gemini-3.1-flash-live-preview over 7
    real voice turns): the LAST ``usage_metadata`` seen before a turn's ``turn_complete`` is
    that turn's usage, one record per turn. Why it is right and not "accumulated":
      * Live sends ONE ``usage_metadata`` per turn, in the same message as ``turn_complete``;
      * its values are the turn's own — ``prompt_token_count`` went 20152, 21510, 22038,
        11348, 11404, 11919, 24399: a running total could never fall from 22038 to 11348;
      * so summing the per-turn records gives what Google bills, with no subtraction.
    A turn's prompt is ~16.4-17.1k tokens of TEXT (system prompt + tool declarations, resent
    every turn) plus the audio (586-3030) and sometimes an image (264/528); at paid prices
    that is US$ 0.009-0.024 per turn. If a future model switches to running totals, change
    :meth:`_turn_tokens` — and only it — to subtract the previous snapshot;
    ``JARVIS_USAGE_RAW=1`` writes every raw message to logs/usage-raw.jsonl to check again.
    """

    def __init__(self):
        self._last = None

    def feed(self, usage_metadata) -> None:
        if usage_metadata is not None:
            self._last = usage_metadata

    def turn_complete(self) -> dict | None:
        snapshot, self._last = self._last, None
        if snapshot is None:
            return None
        return self._turn_tokens(snapshot)

    def _turn_tokens(self, snapshot) -> dict | None:
        return tokens_from_usage(snapshot)


# ═════════════════════════════════════════════════════════════════════════════
#  Records (caller side: build a dict, queue it, return)
# ═════════════════════════════════════════════════════════════════════════════
def _priced(rec: dict, tokens, provider: str, model: str, ts: float) -> dict:
    when = datetime.fromtimestamp(ts, tz=_UTC)
    usd, missing = cost(load_prices(), provider, model, tokens, when)
    billing = get_llm_billing()
    rec.update(billing=billing, tokens=tokens, cost_usd_if_paid=usd,
               cost_usd_actual=0.0 if billing == "free" else usd, price_missing=missing)
    return rec


def _fin_call(rec: dict, ts: float) -> dict:
    """Writer thread: shape a oneshot/tts record."""
    rec["model"] = _norm_model(rec.get("model"))
    rec["result"] = result_of(rec.get("result"))
    return _priced(rec, tokens_from_usage(rec.pop("_usage", None)), rec["provider"], rec["model"], ts)


def record_oneshot(model: str, tier: str | None, result: str, latency_ms: int | None,
                   usage_metadata=None) -> None:
    """One call that went through the Gemini ladder (``result`` is a reason word or an error)."""
    try:
        errlog._enqueue("usage", {"kind": "oneshot", "provider": "gemini", "model": model,
                                  "tier_requested": tier, "result": result, "latency_ms": latency_ms,
                                  "_usage": usage_metadata, "_finalize": _fin_call})
    except Exception:
        pass


def record_tts(model: str, result: str, latency_ms: int | None, usage_metadata=None) -> None:
    """One voice-picker sample (Gemini TTS)."""
    try:
        errlog._enqueue("usage", {"kind": "tts", "provider": "gemini", "model": model,
                                  "tier_requested": None, "result": result, "latency_ms": latency_ms,
                                  "_usage": usage_metadata, "_finalize": _fin_call})
    except Exception:
        pass


class _LiveState:
    """Writer-thread bookkeeping of the voice session in progress."""

    def __init__(self, model: str, start_ts: float):
        self.model = model
        self.start_ts = start_ts
        self.turns = 0
        self.meter = LiveUsageMeter()


_live: _LiveState | None = None
_orphan = _LiveState("gemini-live", 0.0)         # messages that arrive with no session open (should not happen)


def _reset_for_tests() -> None:
    global _live, _orphan
    _live = None
    _orphan = _LiveState("gemini-live", 0.0)
    _prices_cache.clear()


def _fin_session_start(rec: dict, ts: float) -> dict:
    global _live
    model = _norm_model(rec.pop("_model", None))
    _live = _LiveState(model, ts)
    rec["model"] = model
    return rec


def live_session_start(model: str, voice: str, resumed: bool) -> None:
    """A voice session connected (one record; ``resumed`` = it restored the conversation)."""
    errlog.discard_turn()                               # a half-heard sentence of the old connection is not this one's
    try:
        errlog._enqueue("usage", {"kind": "live_session", "provider": "gemini", "event": "start",
                                  "_model": model, "voice": voice, "resumed": bool(resumed),
                                  "_finalize": _fin_session_start})
    except Exception:
        pass


def _fin_session_end(rec: dict, ts: float):
    global _live
    st = _live
    if st is None:                               # a connection that never opened: nothing to close
        return None
    _live = None
    rec.update(model=st.model, duration_s=round(max(0.0, ts - st.start_ts), 1), turns=st.turns)
    return rec


def live_session_end(reason: str, ts: float | None = None) -> None:
    """The voice session ended: ``voluntary`` (reconnect we asked for), ``error`` or ``shutdown``.
    ``ts`` is when it really ended: the connection loop's ``finally`` runs after the backoff sleep
    and after waiting for a new key, which would inflate ``duration_s``, so main.py captures the
    time at the start of the ``except`` and passes it here (``None`` = now)."""
    try:
        errlog._enqueue("usage", {"kind": "live_session", "provider": "gemini", "event": "end",
                                  "end_reason": reason, "_finalize": _fin_session_end}, ts=ts)
    except Exception:
        pass


def _fin_live_msg(rec: dict, ts: float):
    st = _live or _orphan
    st.meter.feed(rec.pop("_um", None))
    if not rec.pop("_tc", False):
        return None
    st.turns += 1
    tokens = st.meter.turn_complete()
    out = {"kind": "live_turn", "provider": "gemini", "model": st.model, "tier_requested": None,
           "result": "ok", "latency_ms": None}
    return _priced(out, tokens, "gemini", st.model, ts)


def _fin_voice_wait(rec: dict, ts: float) -> dict:
    """Writer thread: stamp the model of the open Live session (same lookup as a live turn)."""
    rec["model"] = (_live or _orphan).model
    return rec


def record_voice_wait(rec: dict) -> None:
    """One wait episode (user request -> first sound of the answer), as built by
    ``core.wait_cue.WaitWatchdog``. Never raises, never blocks: one dict on the errlog queue.
    No tokens or cost here: ``summarize()`` ignores it and ``wait_summary()`` reads it."""
    try:
        errlog._enqueue("usage", {"kind": "voice_wait", "provider": "gemini", **rec,
                                  "_finalize": _fin_voice_wait})
    except Exception:
        pass


def live_message(response) -> None:
    """Called for EVERY message of the Live receive loop, on the audio thread. Reads a few
    attributes, appends transcription text to the draft of the turn (errlog.note_speech) and,
    only when the message carries a ``usage_metadata`` or ends a turn, queues a tiny dict
    (measured ~1 microsecond; nothing else happens here — the meter, the price and the disk
    are the writer thread's). Never raises."""
    try:
        um = getattr(response, "usage_metadata", None)
        sc = getattr(response, "server_content", None)
        tc = bool(getattr(sc, "turn_complete", False)) if sc is not None else False
        if sc is not None:
            # The words of the turn in progress, for a "that did not work" report made
            # mid-turn (errlog.note_speech only appends to two bounded lists).
            it, ot = getattr(sc, "input_transcription", None), getattr(sc, "output_transcription", None)
            u = getattr(it, "text", None) if it is not None else None
            a = getattr(ot, "text", None) if ot is not None else None
            if u or a:
                errlog.note_speech(u, a)
            if tc:
                errlog.end_turn()
        if um is None and not tc:
            return
        if os.environ.get("JARVIS_USAGE_RAW") == "1":
            # Diagnostic for research R7: every raw usage_metadata, when it came, and whether
            # the same message ended a turn — to learn if Live sends per-turn or running totals.
            errlog._enqueue("usage_raw", {"has_turn_complete": tc, "usage": um})
        errlog._enqueue("usage", {"_um": um, "_tc": tc, "_finalize": _fin_live_msg})
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════════════
#  Pure: the summary (spec FR-016)
# ═════════════════════════════════════════════════════════════════════════════
def parse_ts(ts, tz) -> datetime | None:
    """ISO timestamp -> aware datetime in ``tz`` (``None`` = the system zone), or None."""
    try:
        return datetime.fromisoformat(str(ts)).astimezone(tz)
    except Exception:
        return None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _new_row(day, provider, model) -> dict:
    return {"day": day, "provider": provider, "model": model, "calls": 0, "ok": 0, "quota_errors": 0,
            "quota_hours": set(), "quota_days": set(), "tokens": dict.fromkeys(TOKEN_FIELDS, 0),
            "calls_with_tokens": 0, "voice_minutes": 0.0, "voice_minutes_estimated": 0.0,
            "sessions_without_end": 0, "cost_usd_if_paid": 0.0,
            "cost_usd_actual": 0.0, "cost_unknown_calls": 0}


def _has_tokens(t) -> bool:
    return isinstance(t, dict) and any(isinstance(v, (int, float)) for v in t.values())


def summarize(records, tz) -> dict:
    """Usage records -> ``UsageSummary``: rows per local day x provider x model with calls,
    ok, quota failures (and the local hours and Google quota days they fell on), summed
    tokens, voice minutes and estimated cost; totals; the models with no price. Records
    that are not dicts or have no usable time are counted in ``skipped``."""
    rows: dict = {}
    quota_events: list = []
    no_price: dict = {}
    billings: set = set()
    sessions: dict = {}                  # live_session id -> start/last-seen/ended, see "sessions without an end"
    skipped = 0
    for r in records:
        if not isinstance(r, dict):
            skipped += 1
            continue
        kind = r.get("kind")
        if kind == "voice_wait":         # latency episodes: read by wait_summary(), not a call, not a skip
            continue
        dt = parse_ts(r.get("ts"), tz)
        if dt is None or kind not in (*_CALL_KINDS, "live_session"):
            skipped += 1
            continue
        provider, model = r.get("provider") or "?", r.get("model") or "?"
        sid = r.get("live_session")
        if sid:
            s = sessions.setdefault(sid, {"start": None, "ended": False, "last": dt, "provider": provider, "model": model})
            s["last"] = max(s["last"], dt)
            if kind == "live_session" and r.get("event") == "start":
                s["start"], s["provider"], s["model"] = dt, provider, model
            elif kind == "live_session" and r.get("event") == "end":
                s["ended"] = True
        if kind == "live_session":
            if r.get("event") == "end":
                row = rows.setdefault((dt.date().isoformat(), provider, model),
                                      _new_row(dt.date().isoformat(), provider, model))
                row["voice_minutes"] += (_num(r.get("duration_s")) or 0.0) / 60.0
            continue
        day = dt.date().isoformat()
        row = rows.setdefault((day, provider, model), _new_row(day, provider, model))
        row["calls"] += 1
        if r.get("billing"):
            billings.add(r["billing"])
        result = r.get("result")
        if result == "ok":
            row["ok"] += 1
        elif result == "quota":
            row["quota_errors"] += 1
            row["quota_hours"].add(dt.hour)
            pday = errlog.pacific_day(dt).isoformat()
            row["quota_days"].add(pday)
            quota_events.append({"ts": dt.isoformat(), "local_day": day, "hour": dt.hour,
                                 "provider_day": pday, "provider": provider, "model": model})
        tokens = r.get("tokens")
        if _has_tokens(tokens):
            row["calls_with_tokens"] += 1
            for k in TOKEN_FIELDS:
                row["tokens"][k] += int(_num(tokens.get(k)) or 0)
        usd, actual = _num(r.get("cost_usd_if_paid")), _num(r.get("cost_usd_actual"))
        if usd is not None:
            row["cost_usd_if_paid"] += usd
        elif result == "ok":
            row["cost_unknown_calls"] += 1
        if actual is not None:
            row["cost_usd_actual"] += actual
        if r.get("price_missing"):
            no_price[model] = no_price.get(model, 0) + 1

    # Sessions without an end. The app can be closed in ways that skip every cleanup
    # (shutdown_jarvis calls os._exit(0); Ctrl-C can too), so a session may have a "start" and
    # never an "end" — measured 2026-10-17: the report said "voz: 0 min" for a 3.5-minute
    # conversation. Its length is estimated as (last record of that session) - (start) and
    # flagged, so the number is useful and honest. A session with no other record counts 0.
    for s in sessions.values():
        if s["start"] is None or s["ended"]:
            continue
        minutes = max(0.0, (s["last"] - s["start"]).total_seconds()) / 60.0
        day = s["last"].date().isoformat()
        row = rows.setdefault((day, s["provider"], s["model"]), _new_row(day, s["provider"], s["model"]))
        row["voice_minutes"] += minutes
        row["voice_minutes_estimated"] += minutes
        row["sessions_without_end"] += 1

    out_rows = []
    totals = {"calls": 0, "ok": 0, "quota_errors": 0, "calls_with_tokens": 0, "voice_minutes": 0.0,
              "voice_minutes_estimated": 0.0, "sessions_without_end": 0, "cost_usd_if_paid": 0.0, "cost_usd_actual": 0.0, "cost_unknown_calls": 0,
              "tokens": dict.fromkeys(TOKEN_FIELDS, 0)}
    for key in sorted(rows):
        row = rows[key]
        for k in ("calls", "ok", "quota_errors", "calls_with_tokens", "voice_minutes",
                  "voice_minutes_estimated", "sessions_without_end",
                  "cost_usd_if_paid", "cost_usd_actual", "cost_unknown_calls"):
            totals[k] += row[k]
        for k in TOKEN_FIELDS:
            totals["tokens"][k] += row["tokens"][k]
        row["quota_hours"] = sorted(row["quota_hours"])
        row["quota_days"] = sorted(row["quota_days"])
        row["voice_minutes"] = round(row["voice_minutes"], 2)
        row["voice_minutes_estimated"] = round(row["voice_minutes_estimated"], 2)
        row["cost_usd_if_paid"] = round(row["cost_usd_if_paid"], 6)
        row["cost_usd_actual"] = round(row["cost_usd_actual"], 6)
        out_rows.append(row)
    totals["voice_minutes"] = round(totals["voice_minutes"], 2)
    totals["voice_minutes_estimated"] = round(totals["voice_minutes_estimated"], 2)
    totals["cost_usd_if_paid"] = round(totals["cost_usd_if_paid"], 6)
    totals["cost_usd_actual"] = round(totals["cost_usd_actual"], 6)
    totals["billing"] = sorted(billings)
    quota_events.sort(key=lambda e: e["ts"])
    return {"rows": out_rows, "totals": totals, "models_without_price": no_price,
            "quota_events": quota_events, "skipped": skipped}
