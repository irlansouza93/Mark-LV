# Implementation Plan: Pastas liberadas (política central de arquivos)

**Branch**: `003-allowed-folders` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/003-allowed-folders/spec.md`

## Summary

Um módulo novo, `core/fs_policy.py`, puro e testável, decide para (caminho, operação, ação) se o
JARVIS pode ler, gravar ou executar, a partir da lista de pastas liberadas (salva por
`config_manager` em `config/api_keys.json`) e de uma lista de negação fixa que nada vence. As oito
ações que recebem caminho do modelo passam a chamar `fs_policy.require(...)` antes de tocar o
disco; recusas viram uma frase `[FOLDER_POLICY]` que o modelo repete na língua do usuário e uma
linha `kind: "policy"` no registro. Executar código do modelo passa a exigir a confirmação real
da interface, que ganha um retorno do resultado ao modelo (`report=True`). ⚙ SETUP ganha um painel
ALLOWED FOLDERS. Decisões técnicas em [research.md](research.md); API em
[contracts/fs_policy.md](contracts/fs_policy.md).

## Technical Context

**Language/Version**: Python 3.14 (`.venv`), PyQt6 para o painel
**Primary Dependencies**: só biblioteca padrão (`os.path.realpath`, `os.walk`, `fnmatch`,
`_winapi` nos testes de junção). Nenhuma dependência nova.
**Storage**: chave `allowed_folders` em `config/api_keys.json` via `memory/config_manager.py`
**Testing**: `pytest` em `tests/` com `tmp_path` e `fs_policy.configure(...)`; nada de rede,
`config/` real ou interface
**Target Platform**: Windows 11 (máquina do Irlan), macOS, Linux
**Project Type**: app de desktop (um processo)
**Performance Goals**: busca em 10 mil arquivos ≤ 10 % mais lenta (SC-005, R7); `check()` de um
caminho < 1 ms (um `realpath`)
**Constraints**: nada novo na thread Qt além do painel (o seletor de pasta é o `QFileDialog` que a
UI já usa); declarações das ferramentas +≈44 caracteres (R10); diff pequeno nos arquivos do
upstream (`actions/*`, `main.py`, `ui.py`)
**Scale/Scope**: 1 usuário; dezenas de pastas liberadas no máximo; ~25 regras de negação

## Constitution Check

*GATE: conferido antes da Fase 0 e de novo depois do desenho (Fase 1).*

| Princípio | Como esta feature cumpre | OK |
|---|---|---|
| I. Evoluir sem reescrever | Módulo novo em `core/`; ações mudam por chamadas pontuais a `require`; `_is_safe_path` do `file_controller` vira uma linha que delega (R9). Painel novo segue o padrão dos overlays de ⚙ SETUP (`MemoryOverlay`) | ✅ |
| II. Spec antes; verificação antes de pronto | Testes escritos antes e vistos falhando (`test_fs_policy*.py`); `py_compile`; medida de desempenho; teste real num único roteiro (spec) | ✅ |
| III. Thread Qt e áudio não esperam | Decisão é síncrona e barata (< 1 ms); confirmação de execução é assíncrona (R6), sem bloquear ferramenta nem sessão | ✅ |
| IV. Segurança proporcional | É a regra "acesso a arquivos só nas pastas liberadas, conferido num ponto central"; execução de código do modelo passa por `core/confirm.py` mostrando o que roda | ✅ |
| V. Conteúdo externo é dado | Lista de negação e liberação só em ⚙ SETUP (FR-019) impedem que um texto lido leve a vazamento ou liberação; a frase de recusa manda não procurar outro caminho | ✅ |
| VI. Declaração custa tokens | Nenhuma ferramenta nova; +≈44 caracteres no total (R10) | ✅ |
| VII. Falha explícita e registrada | Toda recusa diz motivo e como resolver, e é registrada (`kind: "policy"`); configuração corrompida registra e cai no padrão seguro | ✅ |
| VIII. Dados locais, segredos fora do git | Lista fica em `config/api_keys.json` (ignorado); a política protege os próprios segredos do app; registro passa pelo `redact` | ✅ |
| IX. Medido; multiplataforma | SC-005 medido por `tools/fs_policy_bench.py`; `realpath`/`normcase` nos três sistemas; regras de negação por sistema | ✅ |

**Depois da Fase 1:** sem violações. A única ampliação de escopo (confirmação + `report` em
`core/confirm.py`) veio da clarificação de 2026-10-22 e é exigida pelo Princípio IV.

## Project Structure

### Documentation (this feature)

```text
specs/003-allowed-folders/
├── spec.md
├── plan.md              # este arquivo
├── research.md          # R1–R10
├── data-model.md
├── quickstart.md
├── contracts/
│   └── fs_policy.md     # API, ordem da decisão, frase de recusa, mapa ação → operação
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
core/
├── fs_policy.py          # NOVO — a política (puro)
├── confirm.py            # bind(..., deliver=) e request(..., report=)
└── errlog.py             # _kind_for: fs_policy → "policy"
memory/
└── config_manager.py     # get_allowed_folders / save_allowed_folders
actions/
├── file_controller.py    # _is_safe_path → fs_policy; read/write por operação; find via walk
├── code_helper.py        # require em toda leitura/escrita; run/build via confirm(report=True)
├── file_processor.py     # require no arquivo e no destino; confined na extração
├── desktop.py            # require no wallpaper e na Área de Trabalho; tarefa livre via confirm
├── video_player.py       # require(read) em source local
├── browser_control.py    # require(write) no path da captura
├── computer_control.py   # require(write) no path da captura
└── dev_agent.py          # confined nos arquivos; instalar+rodar via confirm(report=True)
plugins/
└── _template.py          # exemplo de uso de fs_policy.require (FR-014)
main.py                   # fs_policy.bind_uploaded(...); confirm.bind(..., deliver=...)
ui.py                     # AllowedFoldersOverlay + botão em ⚙ SETUP
tools/
└── fs_policy_bench.py    # NOVO — medida do SC-005
tests/
├── test_fs_policy.py           # NOVO — decisão, negação, links, confined, upload, recarga
├── test_fs_policy_actions.py   # NOVO — cada ação recusa fora sem tocar o disco
└── test_confirm_report.py      # NOVO — report=True entrega resultado/cancelamento
```

**Structure Decision**: app único; a política é infraestrutura compartilhada em `core/`
(Princípio I), consultada por ações e plugins.

## Fases de implementação (ordem sugerida para o tasks.md)

1. **Fundação (bloqueia tudo):** `fs_policy` + testes (US1, US2 no núcleo), `config_manager`,
   `errlog._kind_for`. Testes primeiro, vistos falhando.
2. **US1/US2 nas ações de arquivo:** `file_controller` (maior uso), com `walk` na busca e medida
   do SC-005.
3. **US3 nas demais ações:** `code_helper`, `file_processor` (+ arquivo enviado, `main.py`),
   `desktop`, `video_player`, `browser_control`, `computer_control`, `dev_agent`; `confirm`
   com `report` e o `deliver` no `main.py`. Ações independentes entre si podem ir em paralelo.
4. **US4:** painel ALLOWED FOLDERS em ⚙ SETUP.
5. **Fechamento:** descrições das ferramentas (R10), `plugins/_template.py`, `CHANGELOG.md`
   (Unreleased), `CLAUDE.md` (linha do mapa do código + armadilha "todo caminho do modelo passa
   por `fs_policy`"), `docs/demandas.md` §2 (estado), `graphify update .`, teste real.

**Modelos:** tarefas das fases 1–4 → `lv-implementer` (Sonnet); fase 5 (docs, CHANGELOG,
descrições) → `lv-quick` (Haiku); revisão → `lv-reviewer`.

## Complexity Tracking

Nenhuma violação a justificar.
