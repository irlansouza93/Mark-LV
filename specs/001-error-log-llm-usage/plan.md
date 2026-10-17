# Implementation Plan: Registro de falhas e medição de uso de LLM

**Branch**: `001-error-log-llm-usage` | **Date**: 2026-10-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-error-log-llm-usage/spec.md`

## Summary

Dar memória às falhas e ao consumo de LLM do JARVIS sem mexer na arquitetura. Três módulos novos
em `core/` — `redact.py` (filtro de segredos), `errlog.py` (registro de falhas, relatos, log de
texto, versão) e `llm_usage.py` (uso, custo, resumo) — gravam JSON Lines em `logs/` por **uma
thread de escrita**, para que quem chama só ponha um `dict` numa fila (< 1 ms). O upstream recebe
só ganchos de uma linha: ~10 em `main.py`, 2 em cada loader, alguns em `core/gemini.py` e 1 em
`core/voice_preview.py`; `ui.py` e as ações existentes não mudam. Falhas que as ações engolem são
pegas por um classificador de "falha mole" no ponto único de despacho (`_execute_tool`). A
ferramenta `report_problem` é um arquivo novo em `actions/` (179 caracteres de texto na
declaração). O resumo de uso é um comando em `tools/`. A lógica pura ganha a primeira base de
`pytest` do projeto. Decisões detalhadas em [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.14 (`.venv`), código compatível com 3.11+ (TaskGroup já exigido pelo app)

**Primary Dependencies**: biblioteca padrão (`logging`, `logging.handlers`, `queue`, `threading`,
`json`, `hashlib`, `re`, `zoneinfo`); `google-genai` 2.29 já instalado (só leitura de
`usage_metadata`). **Nenhuma dependência nova de execução.** `pytest` só em `requirements-dev.txt`.

**Storage**: arquivos JSON Lines em `logs/` (já no `.gitignore`); tabela de preços versionada em
`config/llm_prices.json`

**Testing**: `pytest` em `tests/`, sem rede/chave/microfone; SDK simulado com `SimpleNamespace`;
pasta de logs em `tmp_path`

**Target Platform**: Windows 11 (máquina do Irlan), macOS e Linux — caminhos com `pathlib`,
fuso do Pacífico com fallback puro quando o Windows não tem `tzdata` (research R11)

**Project Type**: app de desktop (PyQt6 + Gemini Live) — infraestrutura em `core/`

**Performance Goals**: < 1 ms por registro na thread de quem chama (p95, medido); HUD segue
< 15 ms/quadro; resumo de 14 dias < 5 s; suíte de testes < 10 s

**Constraints**: nada de I/O na thread Qt nem nos loops de áudio; registrar nunca levanta exceção;
`logs/` limitado (12 MB texto + 25 MB falhas + 3 MB relatos + uso por 12 meses ≈ 55 MB/ano
estimado); nenhum segredo inteiro em disco

**Scale/Scope**: 1 usuário; centenas de turnos de voz por dia (~150 KB/dia de uso estimado);
rajadas de falha limitadas a 20/min por assinatura

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Princípio | Como o plano cumpre | Status |
|---|---|---|
| I. Evoluir sem reescrever | Módulos novos em `core/` (ponto de extensão de infraestrutura); `report_problem` como ação nova auto-descoberta; upstream só com ganchos de 1 linha listados em [contracts/modules.md](contracts/modules.md#ganchos-nos-arquivos-existentes-o-diff-inteiro-do-upstream-é-isto); `ui.py` e ações existentes intocados | ✅ |
| II. Spec antes; verificação antes de "pronto" | Spec + clarify feitos; testes `pytest` da lógica pura escritos antes e vistos falhando (tarefas de teste precedem as de código); ganchos testados sem rede com cliente falso; roteiro de casos do `report_problem` e sessão real de voz no [quickstart](quickstart.md) | ✅ |
| III. Thread Qt e áudio nunca esperam | Fila + thread de escrita (R2); `live_message` só lê atributos e põe na fila; `report_problem` < 5 ms; microbenchmark e `hud_bench` no quickstart | ✅ |
| IV. Segurança proporcional | `report_problem` = leitura (só escreve o log do app); nada de arquivo fora de `logs/`; nenhuma ação irreversível | ✅ |
| V. Conteúdo externo é dado | Registros e relatos são dados; a `lv-triage` já os trata como não confiáveis; o relato não vira memória de perfil | ✅ |
| VI. Declaração custa tokens | Uma ferramenta nova; texto da declaração medido: **179 caracteres** (353 com o esqueleto JSON) — ver "Custo de tokens" | ✅ |
| VII. Falha explícita e registrada | É o objetivo da feature; erro do próprio registro vira aviso único no console/log da tela (nunca silêncio, nunca exceção); escada esgotada vira linha no log da tela (FR-004a) | ✅ |
| VIII. Dados locais, segredos fora do git | Tudo em `logs/` (ignorado); filtro de segredos com teste de chave plantada; transcrição só no relato (clarify Q1); `config/llm_prices.json` não tem segredo | ✅ |
| IX. Medido; multiplataforma | Semântica do `usage_metadata` do Live medida antes de fixar a regra (R7); custo por tabela com data e fonte; fallback de fuso sem `tzdata`; sem API só de Windows | ✅ |

**Resultado:** nenhuma violação; "Complexity Tracking" vazio. Re-checado após o desenho (Fase 1):
continua ✅ — o desenho não acrescentou dependência nem arquivo do upstream além da tabela de ganchos.

## Custo de tokens (Princípio VI / FR-018)

| Ferramenta | Texto da declaração (nome + descrições + parâmetros) | JSON serializado |
|---|---|---|
| `report_problem` | 179 caracteres (medido em 2026-10-16) | 353 caracteres |

Nenhuma regra nova no prompt do sistema (clarify Q5). Remedir na implementação se o texto mudar.

## Project Structure

### Documentation (this feature)

```text
specs/001-error-log-llm-usage/
├── spec.md
├── plan.md              # este arquivo
├── research.md          # decisões R1–R14
├── data-model.md        # formatos dos registros e da tabela de preços
├── quickstart.md        # roteiro de validação
├── contracts/
│   ├── modules.md               # API de core/redact, core/errlog, core/llm_usage + ganchos
│   ├── report_problem-tool.md   # declaração e roteiro de casos
│   └── usage-report-cli.md      # comando de resumo
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
core/
├── redact.py            # NOVO — filtro de segredos (puro)
├── errlog.py            # NOVO — fila + thread de escrita, falhas, relatos, log de texto, tee, versão
├── llm_usage.py         # NOVO — uso de LLM, custo, medidor do Live, resumo, horário da cota
├── gemini.py            # gancho: uso por degrau, falha por degrau, escada esgotada, usage do Live descartável
├── action_loader.py     # gancho: record_failure nos except
├── plugin_loader.py     # gancho: record_failure nos except
└── voice_preview.py     # gancho: record_tts
actions/
└── report_problem.py    # NOVO — ferramenta do relato
config/
└── llm_prices.json      # NOVO — tabela de preços versionada
tools/
└── usage_report.py      # NOVO — resumo de uso (CLI)
memory/
└── config_manager.py    # + get_llm_billing() (research R9)
main.py                  # ganchos de 1 linha (ver contracts/modules.md)
tests/                   # NOVO
├── conftest.py
├── test_redact.py
├── test_errlog_pure.py      # assinatura, frame do projeto, soft failure, agrupamento, versão, retenção, rajada
├── test_errlog_writer.py    # fila → arquivos em tmp_path, rotação, flush, nunca levanta
├── test_llm_usage.py        # tokens_from_usage, custo, LiveUsageMeter, resumo, quota_reset_local
├── test_gemini_hooks.py     # escada com cliente falso
├── test_loader_hooks.py     # ação/plugin de teste que quebram
└── test_report_problem.py   # handler com transcrição e anel falsos
requirements-dev.txt     # NOVO — pytest
```

**Structure Decision**: app único; infraestrutura nova em `core/`, ferramenta nova em `actions/`,
ferramenta de desenvolvimento em `tools/`, testes em `tests/` na raiz (como o `CLAUDE.md` e a
constituição já preveem). Também atualizar `CLAUDE.md` (mapa do código: `core/errlog.py`,
`core/llm_usage.py`, `tests/`), a skill `lv-triage` (formato real dos registros, FR-022) e o
`CHANGELOG.md`.

## Riscos e como o plano os trata

| Risco | Tratamento |
|---|---|
| `usage_metadata` do Live acumulado (contaria em dobro) | medir cru antes (R7); regra num ponto só com teste |
| Tee do console quebrar um `print` com emoji no Windows | o tee nunca levanta; escreve no stream original exatamente como antes |
| Classificador de falha mole com falso positivo | `kind: "soft"` separado; prefixos conservadores; testado com textos reais das ações |
| Thread de escrita morrer | `try/except` por registro; se morrer, `record_*` vira no-op com aviso único |
| Preço desatualizado/ausente | `checked` na tabela; `price_missing` e lista no resumo |
| Merge futuro com o upstream | ganchos de 1 linha, sem reformatar; lista completa no contrato |

## Complexity Tracking

Nenhuma violação da constituição a justificar.
