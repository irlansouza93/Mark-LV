# Contract — registro `voice_wait` e seção do `usage_report`

## Escrita

`core/llm_usage.record_voice_wait(rec: dict) -> None` — nunca levanta, nunca bloqueia; enfileira
`{"kind": "voice_wait", "provider": "gemini", **rec}` no stream `usage` do `errlog` com um
`_finalize` que preenche `model` com o da sessão Live aberta (ou `"gemini-live"`). Campos em
[data-model.md](../data-model.md#voicewaitrecord-logsusage-aaaa-mmjsonl-kind-voice_wait).

Exemplo de linha:

```json
{"v":1,"ts":"2026-10-20T09:12:03.120-04:00","app":"55.2.0+abc1234","pid":1234,"live_session":"86916731","kind":"voice_wait","provider":"gemini","model":"gemini-3.1-flash-live-preview","source":"voice","outcome":"audio","cancel_reason":null,"latency_ms":2310,"tool":true,"thinking":true,"phrase":null,"gave_up":false,"enabled":true,"limits":{"thinking_ms":1500,"phrase_ms":4000,"give_up_ms":20000}}
```

## Leitura

- `summarize(records, tz)` ignora `kind == "voice_wait"` **sem** somar em `skipped` e sem
  contar como chamada (os totais de tokens/custo não mudam).
- `wait_summary(records) -> dict` (puro) — formato em
  [data-model.md](../data-model.md#waitsummary-puro-llm_usagewait_summaryrecords); registros sem
  `latency_ms` (inclusive os `live_turn` antigos) não entram nos percentis.

## `tools/usage_report.py`

- Texto: nova seção ao fim, só quando há ao menos um `voice_wait` no período:

```text
Espera da voz (fim da fala → primeiro áudio), 42 pedidos
  sem ferramenta: 28 · p50 1,1 s · p90 1,9 s · p95 2,3 s
  com ferramenta: 11 · p50 3,4 s · p90 6,0 s · p95 7,2 s
  rede agiu: pensando 17 · "Um momento" 6 (tom 1) · desistência 0 · sem resposta 3
  sugestão de limites: pensando 1,9 s · frase 6,0 s · desistência 20 s  (atual 1,5 / 4 / 20 s)
```

  Com menos de 30 episódios com latência: `sugestão de limites: dados insuficientes (12 de 30)`.
  "atual" vem do `limits` do registro mais recente.
- `--json`: o objeto de saída ganha a chave `"voice_wait"` com o `WaitSummary`.
- Sem `voice_wait` no período: nenhuma linha nova (saída idêntica à de hoje).
