# Research — 002 Aviso de espera garantido

Levantado em 2026-10-19 lendo `main.py`, `core/llm_usage.py`, `core/voice_preview.py`,
`memory/config_manager.py`, `tools/usage_report.py` e os registros da spec 001
(`logs/` do worktree `competent-gauss-4570d1`, 2026-10-17 e 18).

## R0 — O que a spec 001 mede (e o que não mede)

- **Achado:** os 35 registros `live_turn` têm `latency_ms: null` (`_fin_live_msg` grava `None`
  fixo). O `jarvis.log` tem horário em ms, mas só marca ferramentas (`📞`/`🔧`/`📤`) e conexão; a
  fala do usuário só aparece no fim do turno (`You: …`), depois da resposta. **Não há como
  calcular a latência fim-da-fala → primeiro áudio com os dados atuais.**
- **Decision:** a medição faz parte desta feature (FR-007); os limites começam pelos padrões
  da clarificação (1,5 s / 4 s / 20 s) e são recalibrados pela sugestão do resumo.

## R1 — Como saber, no app, que o turno do usuário terminou

- **Fatos:** a sessão usa detecção de fala **no servidor** (VAD automático; `turn_tuning`
  desligado por padrão, `silence_ms` 550 quando ligado). O cliente não recebe um evento "fim da
  fala". O que chega: trechos de `server_content.input_transcription` enquanto o usuário fala
  (o laço já grava `self._last_user_speech` em cada um, `main.py:1719`), depois áudio
  (`response.data`), `tool_call` e `turn_complete`.
- **Decision:** o **último trecho de `input_transcription`** é a âncora do fim da fala. Cada
  trecho novo empurra a âncora; os limites contam a partir dela. Pedido digitado
  (`_on_text_command`) abre o episódio no momento do envio.
- **Rationale:** é o sinal de que o servidor de fato ouviu fala (ruído que o servidor não
  transcreve não abre episódio); não exige VAD local nem novo processamento no callback do
  microfone. Atraso da transcrição só torna o aviso mais conservador (dispara depois, nunca antes).
- **Alternatives considered:** nível do microfone (VAD local) — mais preciso para o instante,
  mas dispara com ruído/eco e mexe no callback de áudio; VAD manual (`activityStart/End`) — muda
  o protocolo da sessão, fora do escopo e arriscado para o merge com o upstream.
- **Regra de exchange:** depois que o primeiro áudio do modelo chegou, trechos atrasados de
  transcrição **não** abrem novo episódio até o `turn_complete` (a transcrição final às vezes
  chega depois do início da resposta).

## R2 — Proactive audio: o modelo pode escolher ficar calado

- **Fato:** `proactive_audio` vem **ligado** (`get_proactive_audio_enabled`), e é o que impede o
  JARVIS de responder à sala. Quando o modelo decide que a fala não era para ele, esperamos que o
  servidor feche o turno (`turn_complete` sem áudio); isso **não foi medido** ainda.
- **Decision:** o episódio fecha com `outcome = no_reply` no `turn_complete` sem áudio, sem frase.
  O roteiro real inclui um caso de fala não dirigida ao JARVIS e confere no `voice_wait` se veio
  `no_reply` (ok) ou `gave_up` (o servidor não fechou o turno).
- **Contingência (só se o roteiro mostrar `gave_up` nesses casos):** antes do merge, exigir para
  a frase de espera e a desistência que o episódio tenha um sinal de que o modelo vai responder
  (ex.: `tool_call` ou `generation` iniciada) ou subir a desistência; decidir com os números do
  roteiro e registrar aqui. Não implementar antes de medir.

## R3 — Onde tocar as frases

- **Decision:** colocar o PCM da frase (24 kHz, int16, em fatias de 2400 bytes) na **mesma
  `audio_in_queue`** do playback da voz, marcando `self._cue_playing = True`.
- **Rationale:** (a) uma única saída de áudio → impossível tocar por cima do modelo; (b) o
  playback já liga `set_speaking(True)`, que **não envia o microfone** ao modelo e abre a cauda
  de eco depois — o modelo nunca "ouve" o "Um momento" e responde a ele; (c) o avatar articula a
  frase pelos visemas do próprio PCM; (d) zero código novo de dispositivo/plataforma.
- **Cortar a frase:** no primeiro `response.data` do modelo com `_cue_playing`, esvaziar a fila
  (só há frase nela) antes de enfileirar o modelo. O que já foi entregue ao dispositivo (≤ 1 lote
  de 200 ms) termina; como é a mesma saída, o modelo toca logo depois — corte, não sobreposição.
- **Fim da frase sem resposta:** hoje o playback só chama `set_speaking(False)` com
  `_turn_done_event` setado; com a frase, o microfone ficaria preso. O ramo de timeout do
  playback passa a liberar também quando `_cue_playing` e a fila esvaziou, e o vigia recoloca o
  HUD em "pensando" se o episódio continua aberto.
- **Alternatives considered:** segundo `OutputStream` — sobreposição possível, duplica a escolha
  de dispositivo e o eco; `sounddevice.play` — idem, e bloqueia/compete com o stream atual.
- **Saída de áudio indisponível:** a frase usa a mesma saída da voz; se a saída falhar, a voz do modelo também falha e o aviso continua sendo o HUD (o erro da saída já é tratado e registrado pelo playback existente).

## R4 — Gerar e guardar as frases

- **Decision:** `voice_preview._synthesise(voice, lang, text=None, style=None)` ganha parâmetros
  opcionais (padrão = comportamento atual). `core/wait_cue.py` guarda
  `config/voice_cache/wait_<kind>_<voice>_<lang>.wav` (`kind` = `hold` | `giveup`), com textos
  por idioma nas mesmas 7 línguas do seletor. `prefetch(voice, lang)` roda numa thread daemon na
  conexão da sessão e na troca de voz; se o arquivo existe, não faz nada. A chamada TTS já é
  registrada em `llm_usage.record_tts` (custo visível no resumo).
- **Reserva:** sem arquivo (primeiro uso, sem rede, cota), toca um **tom** sintetizado (dois
  bipes senoidais suaves ~120 ms, 880→660 Hz, com fade) e a falha do TTS vira `record_failure`
  + uma linha "SYS: wait phrase unavailable … using a tone" (uma vez por voz/idioma por sessão).
- **Reamostragem:** se o TTS devolver taxa ≠ 24 kHz, interpolação linear com numpy.

## R5 — Registro da latência

- **Decision:** um registro novo `kind: "voice_wait"` por episódio, no mesmo `usage-AAAA-MM.jsonl`
  (fila do `errlog`, `_finalize` preenche o modelo da sessão aberta), em vez de preencher o
  `latency_ms` do `live_turn`.
- **Rationale:** um pedido com ferramenta passa por vários `turn_complete` (vários `live_turn`);
  o episódio de espera é um por pedido do usuário. Registro separado evita amarrar os dois
  ciclos. `summarize()` passa a ignorar `voice_wait` sem contá-lo em `skipped`.
- **Latência:** `latency_ms` = âncora → primeiro `response.data` recebido. O atraso do
  dispositivo (medido 213 ms nesta máquina, `🔊 Output latency`) é constante e documentado, não
  somado.

## R6 — Sugestão de limites no resumo

- **Decision (puro, `wait_summary`):** só sugere com ≥ 30 episódios com latência; senão "dados
  insuficientes (N de 30)".
  - HUD = p90 da latência dos turnos **sem** ferramenta, arredondado a 100 ms, faixa 800–3000 ms
    (uma resposta típica rápida não pisca "pensando").
  - Frase = máx(HUD + 1500, p90 de todos), faixa 2500–8000 ms.
  - Desistência = máx(20000, p99 de todos + 5000), faixa até 60000 ms.
- **Rationale:** regras simples, explicáveis no relatório; a decisão final é manual (clarificação Q2).

## R7 — Desistência

- **Decision:** contada a partir de máx(âncora, fim da última ferramenta) e só sem ferramenta
  rodando. Ao vencer: frase `giveup`, `errlog.record_failure("voice", "wait_give_up",
  error_type="soft:no reply", context={latency limits, tool})`, `ui.write_log("SYS: No reply
  from the model — please repeat.")`, HUD volta a `LISTENING` (se não mudo). Episódio fecha com
  `outcome = gave_up`; áudio que chegar depois toca normalmente.

## R8 — Tique do vigia

- **Decision:** tarefa asyncio própria (`_wait_cue_loop`) com `asyncio.sleep(0.1)` criada junto
  dos outros laços da sessão; cada tique chama `watchdog.tick()` e executa as ações. 100 ms de
  resolução cabe nos critérios (SC-001: limite + 0,2 s).
- **Alternatives considered:** reaproveitar o timeout de 100 ms do playback — não tiquetaqueia
  enquanto há áudio na fila e mistura responsabilidades.
