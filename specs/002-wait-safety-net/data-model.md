# Data Model — 002 Aviso de espera garantido

## Limits (configuração, imutável durante um episódio)

| Campo | Tipo | Padrão | Faixa aceita | Origem |
|---|---|---|---|---|
| `enabled` | bool | `true` | — | `config/api_keys.json` → `wait_cue.enabled` |
| `thinking_ms` | int | 1500 | 300–10000 | `wait_cue.thinking_ms` |
| `phrase_ms` | int | 4000 | 1000–30000 | `wait_cue.phrase_ms` |
| `give_up_ms` | int | 20000 | 5000–120000 | `wait_cue.give_up_ms` |

Validação: valor inválido ou fora da faixa → usa o padrão/limite da faixa (como `get_turn_tuning`).
Se `phrase_ms <= thinking_ms`, `phrase_ms = thinking_ms + 500`; se `give_up_ms <= phrase_ms`,
`give_up_ms = phrase_ms + 1000`. Lidos a cada conexão da sessão (mudança vale na próxima conexão).

## WaitEpisode (estado em memória do `WaitWatchdog`)

| Campo | Tipo | Significado |
|---|---|---|
| `source` | `"voice"` \| `"text"` | o que abriu o episódio |
| `anchor` | float (monotônico, s) | último trecho de fala do usuário / envio do texto |
| `thinking_shown` | bool | a ação "thinking" já foi emitida |
| `phrase` | `None` \| `"pending"` \| `"voice"` \| `"tone"` | frase de espera emitida (o `main` informa se tocou voz ou tom) |
| `tool_active` | int | ferramentas em execução agora |
| `tool_seen` | bool | houve `tool_call` antes do primeiro áudio |
| `last_tool_end` | float \| None | fim da última ferramenta (conta a desistência) |
| `audio_at` | float \| None | primeiro áudio do modelo (fecha o episódio) |

### Transições

```text
IDLE ──user_speech/user_text──▶ WAITING(anchor=now)
WAITING ──user_speech──▶ WAITING(anchor=now)                 # fala continua: âncora anda
WAITING ──tick: now-anchor ≥ thinking_ms──▶ ação "thinking" (uma vez)
WAITING ──tick: now-anchor ≥ phrase_ms──▶ ação "phrase" (uma vez)
WAITING ──tick: sem ferramenta e now-max(anchor,last_tool_end) ≥ give_up_ms──▶ ação "give_up" ▶ CLOSED(gave_up)
WAITING ──model_audio──▶ CLOSED(audio, latency=now-anchor) ▶ EXCHANGE
WAITING ──turn_complete──▶ CLOSED(no_reply)
WAITING ──cancel(reason)──▶ CLOSED(cancelled, reason)        # interrupt | muted | asleep | session | disabled
EXCHANGE ──user_speech──▶ ignorado (transcrição atrasada)      # até turn_complete
EXCHANGE ──turn_complete──▶ IDLE
CLOSED(*) ──▶ IDLE (exceto audio → EXCHANGE)
```

Cada `CLOSED` devolve um **VoiceWaitRecord** (abaixo) para o `main` gravar. Com
`enabled = false` o vigia ainda mede (registra `voice_wait`) mas **não emite ações** — a medição
nunca desliga.

## VoiceWaitRecord (`logs/usage-AAAA-MM.jsonl`, `kind: "voice_wait"`)

Campos comuns do registro de uso (`v`, `ts`, `app`, `pid`, `live_session`) vêm do `errlog`.

| Campo | Tipo | Exemplo |
|---|---|---|
| `kind` | `"voice_wait"` | |
| `provider` / `model` | str | `"gemini"` / `"gemini-3.1-flash-live-preview"` (da sessão aberta) |
| `source` | `"voice"` \| `"text"` | `"voice"` |
| `outcome` | `"audio"` \| `"no_reply"` \| `"gave_up"` \| `"cancelled"` | `"audio"` |
| `cancel_reason` | str \| null | `"interrupt"` |
| `latency_ms` | int \| null | `2310` (só em `audio`) |
| `tool` | bool | `true` se houve ferramenta antes do primeiro áudio |
| `thinking` | bool | HUD "pensando" acionado pela rede |
| `phrase` | `"voice"` \| `"tone"` \| null | frase de espera tocada |
| `gave_up` | bool | desistência acionada |
| `enabled` | bool | rede ligada neste episódio |
| `limits` | `{thinking_ms, phrase_ms, give_up_ms}` | limites em vigor |

Retenção: a do arquivo de uso (12 meses, spec 001). Sem transcrição, sem dado pessoal.

## WaitSummary (puro, `llm_usage.wait_summary(records)`)

```text
{
  "episodes": int, "with_latency": int,
  "outcomes": {"audio": n, "no_reply": n, "gave_up": n, "cancelled": n},
  "no_tool": {"n", "p50", "p90", "p95"} | null,      # ms
  "tool":    {"n", "p50", "p90", "p95"} | null,
  "fired": {"thinking": n, "phrase": n, "phrase_tone": n, "gave_up": n},
  "suggested": {"thinking_ms", "phrase_ms", "give_up_ms"} | null,
  "suggest_note": str                                  # "dados insuficientes (12 de 30)" | regra usada
}
```

Regras da sugestão em [research.md R6](research.md#r6--sugestão-de-limites-no-resumo).
Percentil: método do vizinho mais próximo sobre a lista ordenada (determinístico, sem numpy).
