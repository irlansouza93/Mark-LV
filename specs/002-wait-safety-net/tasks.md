---
description: "Task list — 002 Aviso de espera garantido"
---

# Tasks: Aviso de espera garantido ("só um momento")

**Input**: Design documents from `specs/002-wait-safety-net/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: obrigatórios pela constituição (Princípio II) para toda lógica sem interface — escritos
antes e vistos falhando. O que é `main.py` (ganchos de áudio/HUD) é verificado por `py_compile` e
pelo roteiro real do [quickstart.md](quickstart.md).

**Quem executa** (ver `docs/HARNESS.md`): tarefas de código → `lv-implementer` (Sonnet); tarefas
marcadas *(lv-quick)* → `lv-quick` (Haiku); revisão → `lv-reviewer`; teste real e PR → sessão
principal com o Irlan. Nenhum agente faz commit/push/PR.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode rodar em paralelo (arquivos diferentes, sem dependência pendente)
- **[Story]**: história da spec (US1, US2, US3)

---

## Phase 1: Setup

- [x] T001 Rodar a base de testes e anotar quantos passam antes da feature: `.venv/Scripts/python.exe -m pytest tests -q` (registrar o número no fim deste arquivo, seção Notes)

---

## Phase 2: Foundational (bloqueia todas as histórias)

**Purpose**: configuração, o vigia puro e o registro `voice_wait` — tudo testável sem app.

- [x] T002 [P] Escrever `tests/test_wait_config.py` (ver falhar): `get_wait_cue()` com `load_api_keys` trocado por monkeypatch — chave ausente → `{"enabled": True, "thinking_ms": 1500, "phrase_ms": 4000, "give_up_ms": 20000}`; faixas "thinking_ms 300–10000, phrase_ms 1000–30000, give_up_ms 5000–120000" (valor fora → limite da faixa); tipo inválido → padrão; ordem: "se `phrase_ms <= thinking_ms`, `phrase_ms = thinking_ms + 500`; se `give_up_ms <= phrase_ms`, `give_up_ms = phrase_ms + 1000`"; `"enabled": false` respeitado
- [x] T003 Implementar `get_wait_cue() -> dict` em `memory/config_manager.py`, logo depois de `get_turn_tuning()`, no mesmo estilo (`_int` com faixa), docstring explicando cada limite e que a recalibração vem da seção "Espera da voz" do `tools/usage_report.py` (contrato: [contracts/config.md](contracts/config.md)); sem `save_` (depende de T002)
- [x] T004 [P] Escrever `tests/test_wait_cue.py`, parte do vigia (ver falhar), com relógio falso injetado: resposta antes de `thinking_ms` → nenhuma ação e record `outcome="audio"` com `latency_ms`; `"thinking"` em `thinking_ms` e `"phrase"` em `phrase_ms`, cada um uma vez; `turn_complete()` sem áudio → record `no_reply` e nenhuma ação depois; `user_speech()` empurra a âncora; depois do 1º áudio `user_speech()` é ignorado até `turn_complete()`; `user_text()` abre episódio com `source="text"`; desistência só sem ferramenta ativa, contada de máx(âncora, fim da última ferramenta), fecha com `gave_up=True`; `cancel(reason)` → `outcome="cancelled"`, `cancel_reason`; `enabled=False` → nunca emite ação mas devolve records; frase nunca emitida depois do 1º áudio; `tool=True` só se `tool_started()` veio antes do 1º áudio; record traz `limits` (campos em [data-model.md](data-model.md#voicewaitrecord-logsusage-aaaa-mmjsonl-kind-voice_wait))
- [x] T005 Implementar `Limits` (dataclass congelada + `from_config`) e `WaitWatchdog` em `core/wait_cue.py` conforme [contracts/wait_cue.md](contracts/wait_cue.md) e as transições de [data-model.md](data-model.md#transições); métodos de evento O(1), sem I/O, nunca levantam; docstring do módulo explica o porquê (R1: âncora = último trecho de `input_transcription`; R2: proactive audio) (depende de T004)
- [x] T006 [P] Escrever em `tests/test_wait_summary.py` (ver falhar) os testes de escrita: `llm_usage.record_voice_wait(rec)` enfileira no stream `usage` um dict com `kind="voice_wait"`, `provider="gemini"` e, após o `_finalize`, `model` da sessão Live aberta (ou `"gemini-live"` sem sessão); `summarize()` ignora `voice_wait` **sem** somar em `skipped` e sem contar como chamada (siga o padrão de `tests/test_llm_usage.py` para trocar o `errlog`)
- [x] T007 Implementar `record_voice_wait()` (com `_finalize` análogo a `_fin_live_msg`) e o desvio de `voice_wait` em `summarize()` em `core/llm_usage.py` ([contracts/usage-record.md](contracts/usage-record.md)) (depende de T006)

**Checkpoint**: `pytest tests -q` verde; nada no app mudou ainda.

---

## Phase 3: User Story 1 — Sinal visual de que foi ouvido (P1) 🎯 MVP

**Goal**: HUD em "pensando" quando nenhum áudio chega até `thinking_ms`; todo pedido vira um registro `voice_wait` com latência.

**Independent Test**: pedido lento ("pesquise as notícias de hoje sobre IA") → HUD "pensando" ~1,5 s após o fim da fala, sai quando a voz começa; pedido rápido não pisca; `logs/usage-*.jsonl` ganha um `voice_wait` por pedido.

- [x] T008 [US1] Em `main.py`, na conexão da sessão (logo após `llm_usage.live_session_start`): criar `self._wait = WaitWatchdog(Limits.from_config(get_wait_cue()))` e a tarefa `_wait_cue_loop` (asyncio, `sleep(0.1)`, criada junto dos outros laços da sessão e cancelada com eles); no fim/queda da sessão: `cancel("session")` + `llm_usage.record_voice_wait`. No tique: se `self.ui.muted` → `cancel("muted")`; se `self._wake_enabled and not self._awake` → `cancel("asleep")`; ação `"thinking"` → `self.ui.set_state("THINKING")`; ação `"give_up"` → `errlog.record_failure("voice", "wait_give_up", error_type="soft:no reply", context={"limits": ..., "tool": ...})`, `self.ui.write_log("SYS: No reply from the model — please repeat.")`, HUD → `LISTENING` se não mudo, gravar o record; ação `"phrase"` ignorada até a US2. Import de `core.wait_cue` e `get_wait_cue` no topo junto dos outros; mudança pequena e localizada (Princípio I)
- [x] T009 [US1] Em `main.py`, ganchos de evento ([contracts/wait_cue.md](contracts/wait_cue.md#ganchos-no-mainpy-contrato-com-o-código-do-upstream)): `input_transcription` com texto → `self._wait.user_speech()` (ao lado de `self._last_user_speech = …`); `response.data` não interrompido → `model_audio()` e gravar o record; `response.tool_call` → `tool_started()` por chamada e, após `send_tool_response`, `tool_finished()`; `sc.turn_complete` → `turn_complete()`, gravar o record e, se a rede pôs "pensando" e não houve áudio, `ui.set_state("LISTENING")` se não mudo; `interrupt()` → `cancel("interrupt")`; `_on_text_command` (só no ramo que envia) → `user_text()`. Toda chamada protegida para nunca derrubar o laço de recepção (sem `except: pass` silencioso: falha → `errlog.record_failure`) (depende de T008)
- [x] T010 [US1] Verificar: `py_compile` de `main.py`, `pytest tests -q` verde; conferir por leitura que nenhum gancho faz I/O no laço de áudio (só atribuições e `record_voice_wait`, que só enfileira)

**Checkpoint**: MVP — HUD e medição funcionando; frases ainda não tocam.

---

## Phase 4: User Story 2 — Frase de espera quando o silêncio se prolonga (P2)

**Goal**: "Um momento." na voz do JARVIS em `phrase_ms` (uma vez), "Não consegui responder. Pode repetir?" na desistência; tom de reserva sem cache; nunca por cima do modelo.

**Independent Test**: sem olhar a tela, pedido lento sem aviso do modelo → frase aos 4 s, uma vez, cortada se a voz do modelo começar; com o cache apagado, tom na primeira vez e arquivos `config/voice_cache/wait_*` criados em segundo plano.

- [x] T011 [P] [US2] Acrescentar a `tests/test_wait_cue.py` (ver falhar), com `CACHE_DIR` trocado por `tmp_path`: `phrase_path("hold", "Charon", "pt")` → `…/wait_hold_Charon_pt.wav`; `load_phrase` → `None` sem arquivo e int16 a 24 kHz com arquivo; WAV a 16 kHz é reamostrado para 24 kHz; `tone()` é int16, 24 kHz, ~300 ms, não silencioso, pico ≤ 0,3 da escala; `pcm_for` devolve `(pcm, "voice")` com cache e `(tone(), "tone")` sem; `prefetch` com `synth` falso gera só o que falta (`hold` e `giveup`), devolve `None` quando tudo existe, chama `on_error(kind, msg)` quando o `synth` levanta e não deixa arquivo pela metade; `TEXTS` tem `hold` e `giveup` nas 7 línguas do seletor (`pt` = "Um momento." e "Não consegui responder. Pode repetir?")
- [x] T012 [US2] Em `core/voice_preview.py`, `_synthesise(voice, lang, text=None, style=None)`: `text` substitui a frase de `_PHRASES`, `style` substitui o prefixo "Say this warmly and naturally, like a friend:"; sem os parâmetros o comportamento é idêntico ao de hoje (`tests/test_voice_preview_usage.py` continua verde)
- [x] T013 [US2] Em `core/wait_cue.py`: `TEXTS`, `RATE = 24000`, `phrase_path`, `load_phrase`, `tone`, `pcm_for`, `prefetch` (thread daemon; `synth` padrão = `voice_preview._synthesise(voice, lang, text=TEXTS[kind][lang], style="Say this calmly and briefly, like a butler:")`; grava com `voice_preview._write_wav`, atômico) conforme [research.md R4](research.md#r4--gerar-e-guardar-as-frases) (depende de T011, T012)
- [x] T014 [US2] Em `main.py`, na conexão da sessão: `wait_cue.prefetch(voz_atual, voice_preview.language_code(), on_error=…)`; `on_error` → `errlog.record_failure("voice", "wait_phrase_tts", message=msg)` e `ui.write_log("SYS: wait phrase unavailable (…) — using a tone")` no máximo uma vez por voz/idioma por execução. Confirmar que a troca de voz reconstrói a sessão e, portanto, passa por aqui (se não passar, chamar `prefetch` também no ponto da troca) (depende de T013)
- [x] T015 [US2] Em `main.py`, tocar a frase pela fila de playback ([research.md R3](research.md#r3--onde-tocar-as-frases)): ação `"phrase"` → `pcm, how = wait_cue.pcm_for("hold", …)`, enfileirar em fatias de 2400 bytes em `self.audio_in_queue`, `self._cue_playing = True`, `self._wait.phrase_played(how)`; no 1º `response.data` com `_cue_playing` → esvaziar a fila antes de enfileirar o modelo e zerar `_cue_playing`; no ramo de timeout do playback → se `_cue_playing` e fila vazia: `set_speaking(False)`, `_cue_playing = False` e, se `self._wait.waiting`, `ui.set_state("THINKING")`; `interrupt()` zera `_cue_playing`; inicializar `_cue_playing = False` no `__init__` (depende de T014)
- [x] T016 [US2] Em `main.py`, ação `"give_up"` também toca `pcm_for("giveup", …)` pelo mesmo caminho da T015 (antes de pôr o HUD em `LISTENING`) (depende de T015)
- [x] T017 [US2] Verificar: `py_compile` de `main.py`, `core/wait_cue.py`, `core/voice_preview.py`; `pytest tests -q` verde

**Checkpoint**: US1 + US2 funcionando.

---

## Phase 5: User Story 3 — Latência medida e resumida (P3)

**Goal**: o `usage_report` mostra a latência por tipo de turno, quantas vezes a rede agiu e sugere limites.

**Independent Test**: com alguns `voice_wait` em `logs/`, `tools/usage_report.py --days 1` mostra a seção "Espera da voz"; `--json` traz a chave `voice_wait`.

- [x] T018 [P] [US3] Acrescentar a `tests/test_wait_summary.py` (ver falhar): `wait_summary(records)` — percentil pelo vizinho mais próximo sobre a lista ordenada; separa `tool`/`no_tool`; conta `outcomes` e `fired` (`thinking`, `phrase`, `phrase_tone`, `gave_up`); ignora registros que não são `voice_wait` e os sem `latency_ms`; sugestão só com ≥ 30 episódios com latência, senão `suggested=None` e `suggest_note="dados insuficientes (N de 30)"`; regras de [research.md R6](research.md#r6--sugestão-de-limites-no-resumo): HUD = p90 sem ferramenta arredondado a 100 ms na faixa 800–3000; frase = máx(HUD + 1500, p90 de todos) na faixa 2500–8000; desistência = máx(20000, p99 de todos + 5000) até 60000
- [x] T019 [US3] Implementar `wait_summary()` (puro, sem numpy) em `core/llm_usage.py` no formato de [data-model.md](data-model.md#waitsummary-puro-llm_usagewait_summaryrecords) (depende de T018)
- [x] T020 [P] [US3] Acrescentar a `tests/test_usage_report.py` (ver falhar): com `voice_wait` no período, a saída de texto tem a seção "Espera da voz" no formato de [contracts/usage-record.md](contracts/usage-record.md#toolsusage_reportpy) (incluindo "atual" do registro mais recente e a linha "dados insuficientes (N de 30)"); sem `voice_wait`, saída idêntica à de hoje; `--json` traz `"voice_wait"`
- [x] T021 [US3] Implementar a seção em `tools/usage_report.py` (`_report` e o caminho `--json`), números com vírgula decimal como no resto do relatório (depende de T019, T020)

**Checkpoint**: as três histórias funcionando.

---

## Phase 6: Polish & Cross-Cutting

- [x] T022 [P] *(lv-quick)* `CHANGELOG.md`, seção "Unreleased": "Added — wait safety net: HUD thinking state, spoken 'one moment' and give-up phrase when the model stays silent; per-turn latency (`voice_wait`) in the usage log and report (spec 002)"
- [x] T023 [P] *(lv-quick)* `docs/demandas.md` §13 (estado: implementado na spec 002, padrões 1,5/4/20 s, recalibrar pelo `usage_report`), `docs/VISAO.md` (§6: recalibrar os limites após ~1 semana de uso) e `CLAUDE.md` (linha `core/wait_cue.py` no "Mapa do código")
- [x] T024 Skill `lv-validate`: `py_compile` de tudo que mudou, `pytest tests -q` (comparar com o número da T001), `graphify update .`
- [x] T025 Revisão independente com `lv-reviewer` do diff contra a constituição, a spec e as armadilhas do `CLAUDE.md`; corrigir os achados (lv-implementer)
- [x] T028 Correção do teste real ([research.md R4b](research.md#r4b--pronúncia-e-variação-teste-real-de-2026-10-19)) em `core/voice_preview.py` (`_synthesise` com `language_code`, sem prefixo de estilo para texto dado), `core/wait_cue.py` (variantes por tipo e idioma com ≥ 5 palavras, cache `wait_v2_<kind>_<i>_…`, validação de duração e corte de silêncio, sorteio sem repetir, `late_transcript` < 150 ms) e `main.py` (só o que a nova assinatura exigir), com testes antes em `tests/test_wait_cue.py`
- [x] T029 Frases na voz exata da conversa ([research.md R4c](research.md#r4c--frases-na-voz-exata-da-conversa-gerar-pelo-modelo-live)): captura pelo modelo Live em `core/wait_cue.py` (conexão injetável para teste), conferência pela transcrição, cache `wait_v3_<src>_…` com preferência por `live`, TTS como reserva; `main.py` passa o modelo Live ao `prefetch`; testes antes em `tests/test_wait_cue.py`
- [x] T030 Terminar e pausar em vez de cortar ([research.md R3](research.md#r3--onde-tocar-as-frases), FR-005): `main.py` (1º áudio do modelo com `_cue_playing` → silêncio de ~0,2 s + áudio, sem esvaziar a fila); `core/wait_cue.py` (frases de espera curtas, validação `hold` ≤ 2,2 s, ritmo ágil no prompt da captura Live, cache `wait_v4_…` para regerar); testes antes
- [ ] T026 Teste real com o Irlan, num único roteiro: [quickstart.md](quickstart.md) §2–3 (fora do sandbox, com a chave). Se o caso 5 (fala não dirigida) vier `gave_up`, aplicar a contingência de [research.md R2](research.md#r2--proactive-audio-o-modelo-pode-escolher-ficar-calado) e registrar a decisão lá
- [ ] T027 Commit na branch `002-wait-safety-net` e PR para `irlan-dev` com o resultado de cada linha do roteiro e o que não pôde ser verificado (Princípio II); conferir `git status` sem segredos

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T007)** → histórias.
- Dentro da fundação: T002‖T004‖T006 (testes, arquivos diferentes) → T003, T005, T007.
- **US1 (T008–T010)** depende de T003, T005, T007.
- **US2 (T011–T017)**: T011‖T012 podem começar logo após a fundação; T014–T016 dependem de US1 (mesmos pontos do `main.py`).
- **US3 (T018–T021)** depende só de T007 — pode correr em paralelo com US1/US2 (arquivos `core/llm_usage.py`, `tools/usage_report.py`, testes próprios). Evitar editar `core/llm_usage.py` ao mesmo tempo que T007.
- **Polish**: T022‖T023 a qualquer momento depois da US2; T024 → T025 → T026 → T027.
- `main.py` é tocado por T008, T009, T014, T015, T016: **sempre em sequência**, um agente por vez.

## Parallel Example

```text
# Fundação — três testes em paralelo (lv-implementer):
T002 tests/test_wait_config.py
T004 tests/test_wait_cue.py (vigia)
T006 tests/test_wait_summary.py (escrita)

# Depois da fundação — US3 em paralelo com US1:
lv-implementer A: T008 → T009 → T010 (main.py)
lv-implementer B: T018 → T019 → T020 → T021 (llm_usage.py / usage_report.py)
```

## Implementation Strategy

1. **MVP** = Fundação + US1: HUD "pensando" + medição. Já resolve "ele me ouviu?" e começa a
   gerar o dado de latência.
2. **US2**: frases na voz do JARVIS — cobre quem não olha a tela.
3. **US3**: o resumo que permite recalibrar os limites com número medido (Princípio IX).
4. Um único teste real no fim (T026), com todas as histórias juntas (`docs/HARNESS.md`, regra 7).

## Notes

- Baseline de testes (T001, 2026-10-19): 519 passed, 1 skipped (11 s).
- Commits só pela sessão principal; agentes não fazem commit/push/PR.
