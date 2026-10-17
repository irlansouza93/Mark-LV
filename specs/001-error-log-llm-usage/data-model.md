# Data Model — 001 Registro de falhas e medição de uso de LLM

Todos os registros são **uma linha JSON por evento** (JSON Lines, UTF-8), gravados em `logs/` pela
thread de escrita depois do filtro de segredos. Campos comuns a todo registro:

| Campo | Tipo | Regra |
|---|---|---|
| `v` | int | versão do formato do registro; começa em `1` (a `lv-triage` confere) |
| `ts` | str | horário ISO 8601 com fuso local (`2026-10-16T15:40:02.123-03:00`) |
| `app` | str | versão do JARVIS (`55.1.0+8f9b865`, ver research R6) |
| `pid` | int | processo, para separar execuções |
| `live_session` | str \| null | id curto da sessão de voz ativa (se houver) |

Textos livres (`message`, `context.*`, `args`, `result`, `transcript`) são cortados em 500
caracteres (`traceback` em 8 000), com `…[+N chars]` no fim.

## Failure — `logs/failures.jsonl`

| Campo | Tipo | Regra |
|---|---|---|
| `kind` | enum | `exception` · `soft` · `gemini` · `live` · `uncaught` · `logger` |
| `component` | enum | `action` · `plugin` · `inline_tool` · `tool` (falha mole vista no despacho, sem saber o tipo) · `gemini` · `live` · `thread` · `app` |
| `name` | str | nome da ação/plugin/ferramenta, modelo do degrau, `ladder` ou `live_connect` |
| `phase` | enum \| null | `load` · `run` · `connect` · `receive` · `resume` (quando se aplica) |
| `error_type` | str | classe da exceção ou motivo classificado (`quota`, `unavailable`, `gone`, `timeout`, `ladder_exhausted`, `soft:<prefixo>`) |
| `message` | str | mensagem do erro (filtrada, cortada) |
| `traceback` | str \| null | traceback formatado (só `exception`/`uncaught`/`live`) |
| `origin` | str | `arquivo/relativo.py:função` do frame mais interno do projeto |
| `origin_line` | int \| null | linha desse frame (fora da assinatura) |
| `signature` | str | 12 hex, ver research R5 |
| `context` | obj | específico por componente (abaixo) |
| `suppressed` | int \| absent | quantos iguais foram omitidos no último minuto (rajada) |
| `dropped` | int \| absent | registros descartados por fila cheia desde o último gravado |

`context` por componente:
- `action`/`plugin`/`inline_tool`: `{args: {...filtrado}}`; em `soft`, também `result`.
- `gemini` (degrau): `{tier, timeout_ms, latency_ms}`; em `ladder_exhausted`: `{tier, tried: [model, …], reasons: {model: reason}}`.
- `live`: `{model, voice, resumed: bool, attempt: int, backoff_s, classified: "quota|handle|net|key|config|other"}`.

## ProblemReport — `logs/reports.jsonl`

| Campo | Tipo | Regra |
|---|---|---|
| `kind` | const | `report` |
| `expected` | str | o que o usuário esperava, nas palavras dele (obrigatório; vazio → `"(not said)"`) |
| `happened` | str \| null | o que deu errado, na descrição do modelo/usuário |
| `transcript` | list[str] | últimas ~3 trocas (≤ 6 linhas `User: …` / `<Asst>: …`), cada uma cortada |
| `recent_tools` | list[obj] | últimas 8: `{ts, name, args, result, soft_failure: bool}` |
| `recent_failures` | list[obj] | falhas dos últimos 10 min: `{ts, signature, component, name, error_type}` |

Falhas automáticas **nunca** têm `transcript` (FR-017).

## UsageRecord — `logs/usage-YYYY-MM.jsonl`

| Campo | Tipo | Regra |
|---|---|---|
| `kind` | enum | `oneshot` (escada) · `live_turn` (conversa) · `tts` (amostra de voz) · `live_session` (abertura/fechamento) |
| `provider` | enum | `gemini` (hoje) · `deepseek` · `openai` (spec 004) |
| `model` | str | modelo que respondeu (ou que falhou) |
| `tier_requested` | str \| null | `fast` · `smart` · `search` · nome explícito (só `oneshot`) |
| `billing` | enum | `free` · `paid` (research R9) |
| `result` | enum | `ok` · `quota` · `unavailable` · `gone` · `timeout` · `error` |
| `latency_ms` | int \| null | do envio até a resposta (ou falha) |
| `tokens` | obj \| null | `{in_text, in_audio, in_image, in_cached, out_text, out_audio, thoughts, total}`; campo desconhecido = `null`, não `0` |
| `cost_usd_if_paid` | float \| null | custo pela tabela na hora; `null` se modelo sem preço ou sem tokens |
| `cost_usd_actual` | float \| null | `0.0` quando `billing=free`; igual ao anterior quando `paid` |
| `price_missing` | bool | `true` quando o modelo não está na tabela |

`live_session` (dois registros por sessão): `{event: "start"|"end", model, voice, resumed, duration_s (no end), turns (no end), end_reason: "voluntary|error|shutdown"}`.

Regra de modalidade: `prompt_tokens_details`/`response_tokens_details` com `modality` TEXT →
`in_text`/`out_text`, AUDIO → `in_audio`/`out_audio`, IMAGE/VIDEO → `in_image`. Sem detalhes:
tudo no `*_text` e o preço de texto. `thoughts_token_count` é cobrado como saída de texto.

## PriceTable — `config/llm_prices.json`

```json
{
  "v": 1,
  "checked": "2026-10-16",
  "source": ["https://ai.google.dev/gemini-api/docs/pricing", "…"],
  "unit": "usd_per_1m_tokens",
  "models": {
    "gemini/gemini-2.5-flash-lite": {"in": 0.10, "out": 0.40},
    "gemini/gemini-3.1-flash-live-preview": {"in": {"text": 0.75, "audio": 3.00},
                                              "out": {"text": 4.50, "audio": 12.00}},
    "deepseek/deepseek-flash": {"in": 0.15, "in_cached": 0.003, "out": 0.60,
                                "peak_multiplier": 2.0}
  }
}
```

Validação: chave `provider/model`; `in`/`out` número ou objeto por modalidade; preço negativo ou
tipo errado → entrada ignorada com aviso no log de texto (nunca exceção). Nome de modelo com
prefixo `models/` é normalizado antes de buscar.

## FailureGroup (calculado, não gravado)

`{signature, count, first_ts, last_ts, versions: [..], component, name, error_type, origin, example}` — produzido por `group(records)` (pura), usado pela `lv-triage` e por testes.

## UsageSummary (calculado, não gravado)

Por dia local × provedor × modelo: `calls`, `ok`, `quota_errors`, `quota_hours` (horas locais com
falta de cota), `tokens` somados por campo, `voice_minutes` (de `live_session.end`),
`cost_usd_if_paid`, `cost_unknown_calls`; mais totais do período e lista `models_without_price`.

## Transições

- **Sessão de voz:** `start` (conectou) → N × `live_turn` → `end` (motivo). Reconexão com retomada
  abre uma sessão nova com `resumed: true`.
- **Fila de escrita:** `enqueued` → `written` | `dropped` (fila > 10 000) | `suppressed` (rajada).
