# Implementation Plan: Aviso de espera garantido ("só um momento")

**Branch**: `002-wait-safety-net` | **Date**: 2026-10-19 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-wait-safety-net/spec.md`

## Summary

Um **vigia de espera** puro (`core/wait_cue.py`, relógio injetável) acompanha cada turno do
usuário: abre um episódio no fim da fala (último trecho de `input_transcription`) ou no envio de
um pedido digitado, e devolve ações quando os limites vencem — HUD "pensando" (1,5 s), **frase de
espera** "Um momento." (4 s, uma vez) e **desistência** "Não consegui responder. Pode repetir?"
(20 s, sem ferramenta rodando). O episódio fecha no primeiro áudio do modelo, no `turn_complete`,
em interrupção, mudo, sono ou queda da sessão, e vira um registro `voice_wait` no arquivo de uso
da spec 001 com a latência até o primeiro áudio. As frases são geradas uma vez por voz e idioma
pelo TTS que o seletor de voz já usa (`core/voice_preview.py`) e tocadas **pela mesma fila de
playback da voz** — o que garante, de graça, que nunca se sobrepõem ao modelo, que o microfone
não as envia ao modelo e que o avatar as articula. O resumo de uso (`tools/usage_report.py`)
ganha a seção "Espera da voz" com p50/p90/p95 por tipo de turno e a sugestão de limites.

## Technical Context

**Language/Version**: Python 3.14 (`.venv/`)

**Primary Dependencies**: as já existentes — `google-genai` (Live + TTS), `numpy` (PCM/tom),
`sounddevice` (playback atual), PyQt6 (só via fachada `JarvisUI`). **Nenhuma dependência nova.**

**Storage**: `config/voice_cache/wait_<kind>_<voice>_<lang>.wav` (já no `.gitignore`);
registros em `logs/usage-AAAA-MM.jsonl` (spec 001); limites em `config/api_keys.json`, chave
`wait_cue`.

**Testing**: `pytest` em `tests/` (vigia com relógio falso, cache/tom, config, resumo, relatório);
`py_compile` no que mudou; teste real com a chave num único roteiro ([quickstart.md](quickstart.md)).

**Target Platform**: desktop Windows/macOS/Linux (mesma saída de áudio da voz).

**Project Type**: desktop-app (processo único PyQt6 + asyncio).

**Performance Goals**: custo no laço de recepção ≈ alguns µs por mensagem (só atribuições e
comparações); o tique do vigia roda a cada 100 ms numa tarefa asyncio própria (~1 µs por tique).
HUD sem mudança (reusa o estado `THINKING`).

**Constraints**: zero tokens a mais (nada vai ao modelo); nada bloqueia o loop de áudio nem a
thread Qt; a geração das frases (≈4 s cada, medido na spec do seletor de voz) roda em thread
daemon, nunca no caminho do turno.

**Scale/Scope**: 1 usuário; ~35 turnos de voz em 2 dias de medição (spec 001) → registros
`voice_wait` de algumas centenas por mês.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Princípio | Como o plano cumpre | Status |
|---|---|---|
| I. Evoluir sem reescrever | Módulo novo em `core/`; ganchos curtos e localizados em `main.py` (~40 linhas em 6 pontos); `voice_preview._synthesise` ganha só um parâmetro opcional; `ui.py` não muda. | ✅ |
| II. Spec antes; verificação antes de "pronto" | Lógica pura (vigia, cache/tom, config, resumo) com `pytest` escrito antes e visto falhando; comportamento real conferido no roteiro do quickstart, fora do sandbox. | ✅ |
| III. Thread Qt e áudio nunca esperam | Ganchos no laço de recepção = atribuições; tique em tarefa asyncio própria; TTS em thread daemon; HUD só via `ui.set_state`. É exatamente a regra "silêncio depois de uma pergunta é falha". | ✅ |
| IV. Segurança proporcional | Não é ação nem plugin; não faz nada irreversível. | n/a |
| V. Conteúdo externo é dado | Nenhum conteúdo externo é lido. | n/a |
| VI. Declaração custa tokens | Nenhuma ferramenta nova, nenhuma mudança no prompt: 0 caracteres somados. | ✅ |
| VII. Falha explícita e registrada | Desistência → falha `soft` no `failures.jsonl` + linha no log da tela; falha do TTS da frase → registro + linha "usando tom" (uma vez); nada de `except: pass` no caminho do usuário. | ✅ |
| VIII. Dados locais, segredos fora do git | Frases são texto fixo (sem dado pessoal) e ficam em `config/voice_cache/` (ignorado); registros sem transcrição. | ✅ |
| IX. Medido; multiplataforma | Padrões citam a origem (demandas §13) e o plano gera a medição que os recalibra; latência medida em ms por turno. Sem código de um só sistema. | ✅ |

**Re-check pós-design (Phase 1):** sem violações; a tabela "Complexity Tracking" fica vazia.

## Project Structure

### Documentation (this feature)

```text
specs/002-wait-safety-net/
├── plan.md              # este arquivo
├── research.md          # Phase 0: decisões R1–R8
├── data-model.md        # Phase 1: episódio, limites, registro voice_wait, resumo
├── quickstart.md        # Phase 1: roteiro único de teste real
├── contracts/
│   ├── wait_cue.md      # API do módulo core/wait_cue.py
│   ├── config.md        # chave wait_cue em config/api_keys.json
│   └── usage-record.md  # registro voice_wait + seção do usage_report
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
core/
├── wait_cue.py          # NOVO: WaitWatchdog (puro), Limits, frases (cache, prefetch, tom)
├── voice_preview.py     # _synthesise(voice, lang, text=None, style=None) — parâmetros opcionais
└── llm_usage.py         # record_voice_wait() + wait_summary() puro; summarize() ignora voice_wait sem contar como "skipped"
memory/
└── config_manager.py    # get_wait_cue() com limites e faixas
main.py                  # ganchos: recepção, ferramenta, texto digitado, playback, tique, (re)conexão
tools/
└── usage_report.py      # seção "Espera da voz" (texto e --json)
tests/
├── test_wait_cue.py         # NOVO: vigia com relógio falso; frases (cache, tom, reamostragem)
├── test_wait_summary.py     # NOVO: wait_summary + summarize ignorando voice_wait
├── test_wait_config.py      # NOVO: get_wait_cue (padrões, faixas, desligado)
└── test_usage_report.py     # + seção "Espera da voz"
```

**Structure Decision**: app de desktop único; o comportamento novo mora num módulo de
infraestrutura em `core/` (Princípio I) e o `main.py` só liga os eventos que já passam por ele
ao vigia e executa as ações que o vigia devolve.

## Fases de implementação (ordem sugerida para o tasks.md)

1. **Fundação (bloqueante):** `get_wait_cue()`; `WaitWatchdog` + `Limits` com testes; registro
   `voice_wait` (`record_voice_wait`).
2. **US1 (P1, MVP):** ganchos no `main.py` (fala, texto, áudio, ferramenta, `turn_complete`,
   interrupção, mudo/sono, reconexão) + tarefa de tique + ação "thinking" → `ui.set_state`.
   Já grava `voice_wait` com latência (parte de US3 sai de graça aqui).
3. **US2 (P2):** frases — `_synthesise` com texto; cache, prefetch na conexão e na troca de voz,
   tom de reserva; tocar pela fila de playback, cortar no primeiro áudio, liberar o microfone ao
   fim da frase; desistência (frase + falha `soft` + linha no log + HUD volta a ouvir).
4. **US3 (P3):** `wait_summary()` + seção do `usage_report` (texto e `--json`) com sugestão.
5. **Fechamento:** `CHANGELOG.md` (Unreleased), `docs/demandas.md` §13 e `docs/VISAO.md`
   (estado), `graphify update .`, roteiro real, PR para `irlan-dev`.

## Complexity Tracking

*Sem violações da constituição.*
