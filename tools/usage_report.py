"""
Resumo de uso de LLM do Mark LV (ferramenta de desenvolvimento, nao faz parte do app).

Le logs/usage-YYYY-MM.jsonl (escritos por core/llm_usage.py) e imprime, por dia, provedor
e modelo: chamadas, falta de cota, tokens, minutos de voz e custo estimado se fosse pago.
Com registros "voice_wait" (aviso de espera da voz) no periodo, acrescenta a secao "Espera da voz":
latencias p50/p90/p95, o que a rede de seguranca fez e sugestao de limites.
Nao chama nenhum servico: so le arquivos locais.

    .venv/Scripts/python.exe tools/usage_report.py [--days N | --from AAAA-MM-DD [--to AAAA-MM-DD]]
                                                   [--by day|model|provider] [--json] [--logs PASTA]

Codigo de saida: 0 (com ou sem registros), 2 para argumentos invalidos. Linhas corrompidas
(corte por queda de energia) sao ignoradas e contadas no rodape.
Contrato: specs/001-error-log-llm-usage/contracts/usage-report-cli.md
          e specs/002-wait-safety-net/contracts/usage-record.md (secao Espera da voz)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:                  # running as a script puts tools/ first, not the repo root
    sys.path.insert(0, str(ROOT))

from core import llm_usage  # noqa: E402

DEFAULT_DAYS = 14


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def _read_records(logs: Path, start: date, end: date):
    """Records whose LOCAL day falls in [start, end], plus how many lines were unreadable.
    Months are filtered by file name first (one file per month), with a day of slack on both
    sides because a file is named by the month the record was written in."""
    corrupt = 0
    wanted, d = set(), start - timedelta(days=1)
    while d <= end + timedelta(days=1):
        wanted.add(d.strftime("%Y-%m"))
        d += timedelta(days=1)
    records = []
    for month in sorted(wanted):
        path = logs / f"usage-{month}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                corrupt += 1
                continue
            if not isinstance(rec, dict):
                corrupt += 1
                continue
            dt = llm_usage.parse_ts(rec.get("ts"), None)
            if dt is not None and not (start <= dt.date() <= end):
                continue
            records.append(rec)                 # a record with no usable time is kept so summarize() counts it as skipped
    return records, corrupt


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def _merge(rows, key_fn):
    """Aggregate summary rows by ``key_fn(row)`` (for --by model / provider)."""
    merged: dict = {}
    for r in rows:
        m = merged.get(key_fn(r))
        if m is None:
            m = merged[key_fn(r)] = {**r, "tokens": dict(r["tokens"]), "quota_hours": [], "quota_days": []}
            continue
        for k in ("calls", "ok", "quota_errors", "calls_with_tokens", "voice_minutes",
                  "voice_minutes_estimated", "sessions_without_end",
                  "cost_usd_if_paid", "cost_usd_actual", "cost_unknown_calls"):
            m[k] += r[k]
        for k in r["tokens"]:
            m["tokens"][k] += r["tokens"][k]
        if m.get("model") != r.get("model"):
            m["model"] = "(varios)"
        if m.get("provider") != r.get("provider"):
            m["provider"] = "(varios)"
    return list(merged.values())


def _table(rows, by: str) -> list[str]:
    cols = {"day": ["dia", "provedor", "modelo"], "model": ["modelo", "provedor"], "provider": ["provedor"]}[by]
    head = cols + ["chamadas", "cota", "tokens in/out", "voz min", "US$ se pago"]
    keys = {"dia": "day", "provedor": "provider", "modelo": "model"}
    body = []
    for r in rows:
        t = r["tokens"]
        tin, tout = t["in_text"] + t["in_audio"] + t["in_image"], t["out_text"] + t["out_audio"] + t["thoughts"]
        tok = f"{_fmt_tokens(tin)} / {_fmt_tokens(tout)}" if (tin or tout) else "-"
        voice = f"{round(r['voice_minutes'])}" if r["voice_minutes"] else "-"
        usd = f"{r['cost_usd_if_paid']:.2f}" + ("*" if r["cost_unknown_calls"] else "")
        body.append([str(r[keys[c]]) for c in cols] +
                    [str(r["calls"]), str(r["quota_errors"]), tok, voice, usd])
    widths = [max(len(head[i]), *(len(row[i]) for row in body)) if body else len(head[i])
              for i in range(len(head))]
    left = len(cols)                            # text columns on the left, numbers right-aligned

    def line(cells):
        return "  ".join(c.ljust(w) if i < left else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths))).rstrip()

    return [line(head)] + [line(r) for r in body]


def _secs(ms, decimals: bool = True) -> str:
    """Milliseconds -> seconds with a decimal comma ("1,9"); without ``decimals`` a whole number
    of seconds drops the ",0" ("20"), as the limits are usually written."""
    text = f"{ms / 1000:.1f}".replace(".", ",")
    return text[:-2] if not decimals and text.endswith(",0") else text


def _latest_limits(records):
    """``limits`` of the most recent voice_wait record (by time; a record with no usable time
    ranks oldest), or None. These are the limits the app was running with, to compare with the
    suggestion."""
    best, best_ts = None, None
    for r in records:
        if not isinstance(r, dict) or r.get("kind") != "voice_wait" or not isinstance(r.get("limits"), dict):
            continue
        dt = llm_usage.parse_ts(r.get("ts"), None)
        if best is None or (dt is not None and (best_ts is None or dt >= best_ts)):
            best, best_ts = r["limits"], dt
    return best


def _wait_lines(ws: dict, current) -> list[str]:
    """The "Espera da voz" section (contract: specs/002-wait-safety-net/contracts/usage-record.md)."""
    out = [f"Espera da voz (fim da fala → primeiro áudio), {ws['episodes']} pedidos"]
    for label, key in (("sem ferramenta", "no_tool"), ("com ferramenta", "tool")):
        g = ws[key]
        if g:
            out.append(f"  {label}: {g['n']} · p50 {_secs(g['p50'])} s · p90 {_secs(g['p90'])} s · p95 {_secs(g['p95'])} s")
        else:
            out.append(f"  {label}: 0")
    f, o = ws["fired"], ws["outcomes"]
    out.append(f"  rede agiu: pensando {f['thinking']} · \"Um momento\" {f['phrase']} (tom {f['phrase_tone']})"
               f" · desistência {f['gave_up']} · sem resposta {o['no_reply']}")
    s = ws["suggested"]
    if s is None:
        out.append(f"  sugestão de limites: {ws['suggest_note']}")
    else:
        line = (f"  sugestão de limites: pensando {_secs(s['thinking_ms'])} s · frase {_secs(s['phrase_ms'])} s"
                f" · desistência {_secs(s['give_up_ms'], False)} s")
        if current:
            try:
                line += "  (atual " + " / ".join(_secs(current[k], False) for k in ("thinking_ms", "phrase_ms", "give_up_ms")) + " s)"
            except (KeyError, TypeError):
                pass                            # a limits dict from some future version: skip the comparison
        out.append(line)
    return out


def _report(summary: dict, start: date, end: date, days: int, checked, corrupt: int, by: str,
            wait=None) -> str:
    out = []
    when = f"precos conferidos em {checked}".replace("precos", "preços") if checked else "tabela de preços ausente"
    out.append(f"Uso de LLM - {start} a {end} ({days} dias)   {when}")
    out.append("")
    rows = summary["rows"]
    if by == "model":
        rows = _merge(rows, lambda r: (r["provider"], r["model"]))
    elif by == "provider":
        rows = _merge(rows, lambda r: r["provider"])
    out += _table(rows, by)
    out.append("")
    events: dict = {}
    for e in summary["quota_events"]:
        d = events.setdefault(e["local_day"], {"hours": set(), "models": set(), "pdays": set()})
        d["hours"].add(e["hour"])
        d["models"].add(e["model"])
        d["pdays"].add(e["provider_day"])
    for day in sorted(events):
        d = events[day]
        hours = ", ".join(f"{h}h" for h in sorted(d["hours"]))
        out.append(f"Cota grátis esgotou: {day} às {hours} ({', '.join(sorted(d['models']))}); "
                   f"dia de cota do Google (Pacífico): {', '.join(sorted(d['pdays']))}")
    t = summary["totals"]
    tier = {"free": "nível grátis", "paid": "nível pago"}
    billings = t.get("billing") or ["free"]
    level = tier[billings[0]] if len(billings) == 1 and billings[0] in tier else "níveis grátis e pago"
    out.append(f"Total do período: US$ {t['cost_usd_if_paid']:.2f} se pago "
               f"(real: US$ {t['cost_usd_actual']:.2f}, {level})")
    if t["calls"]:
        pct = 100.0 * t["calls_with_tokens"] / t["calls"]
        voice = f"voz: {t['voice_minutes']:.0f} min"
        if t["sessions_without_end"]:
            n = t["sessions_without_end"]
            voice += (f" (inclui {t['voice_minutes_estimated']:.0f} min estimados de {n} "
                      f"sessão sem fim registrado)" if n == 1 else
                      f" (inclui {t['voice_minutes_estimated']:.0f} min estimados de {n} "
                      f"sessões sem fim registrado)")
        out.append(f"Chamadas com contagem de tokens: {t['calls_with_tokens']} de {t['calls']} ({pct:.0f}%); {voice}")
    if t["cost_unknown_calls"]:
        out.append(f"* custo desconhecido em {t['cost_unknown_calls']} chamada(s) (sem preço ou sem contagem de tokens)")
    if summary["models_without_price"]:
        names = ", ".join(f"{m} ({n} chamada{'s' if n != 1 else ''})"
                          for m, n in sorted(summary["models_without_price"].items()))
        out.append(f"Modelos sem preço na tabela (custo desconhecido): {names}")
    if corrupt:
        out.append(f"{corrupt} linha(s) corrompida(s) ignorada(s)")
    if summary["skipped"]:
        out.append(f"{summary['skipped']} registro(s) sem horário válido ignorado(s)")
    if wait:
        out += ["", *wait]
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="usage_report", description="Resumo de uso de LLM do Mark LV.")
    parser.add_argument("--days", type=int, help=f"últimos N dias locais (padrão {DEFAULT_DAYS})")
    parser.add_argument("--from", dest="from_", help="início do período, AAAA-MM-DD")
    parser.add_argument("--to", help="fim do período, AAAA-MM-DD (padrão: hoje)")
    parser.add_argument("--by", choices=("day", "model", "provider"), default="day", help="agrupamento da tabela")
    parser.add_argument("--json", action="store_true", help="imprime o resumo em JSON")
    parser.add_argument("--logs", default=str(ROOT / "logs"), help="pasta dos registros")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:                     # argparse exits on a bad argument; callers get the code back
        return int(e.code or 0)

    def bad(msg: str) -> int:
        print(f"usage_report: {msg}", file=sys.stderr)
        return 2

    today = date.today()
    try:
        if args.days is not None and (args.from_ or args.to):
            return bad("--days não pode ser usado junto com --from/--to")
        if args.to and not args.from_:
            return bad("--to precisa de --from")
        if args.from_:
            start = _parse_day(args.from_)
            end = _parse_day(args.to) if args.to else today
            if start > end:
                return bad("--from é depois de --to")
        else:
            days = args.days if args.days is not None else DEFAULT_DAYS
            if days < 1:
                return bad("--days precisa ser 1 ou mais")
            end, start = today, today - timedelta(days=days - 1)
    except ValueError:
        return bad("data inválida (use AAAA-MM-DD)")
    days = (end - start).days + 1

    try:
        sys.stdout.reconfigure(errors="replace")     # keep the console's own encoding; never crash on a character
    except Exception:
        pass

    records, corrupt = _read_records(Path(args.logs), start, end)
    summary = llm_usage.summarize(records, None)
    wait_summary = llm_usage.wait_summary(records)
    wait = _wait_lines(wait_summary, _latest_limits(records)) if wait_summary["episodes"] else None
    checked = llm_usage.load_prices().get("checked")
    if args.json:
        payload = dict(summary, period={"from": str(start), "to": str(end), "days": days},
                       prices_checked=checked, corrupt_lines=corrupt, voice_wait=wait_summary)
        print(json.dumps(payload, ensure_ascii=True, indent=2, default=str))
        return 0
    if not summary["rows"]:
        print(f"nenhum registro no período ({start} a {end})")
        if corrupt:
            print(f"{corrupt} linha(s) corrompida(s) ignorada(s)")
        if wait:                                 # a period with only wait records still has something to show
            print()
            print("\n".join(wait))
        return 0
    print(_report(summary, start, end, days, checked, corrupt, args.by, wait))
    return 0


if __name__ == "__main__":
    sys.exit(main())
