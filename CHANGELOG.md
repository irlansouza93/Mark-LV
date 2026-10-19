# Changelog — Mark LV (fork irlansouza93)

Versões deste fork, independentes do upstream (FatihMakes) e do Mark LVI.
Formato inspirado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).
Esquema: `55.MINOR.PATCH` — 55 é o Mark LV; MINOR sobe a cada feature mesclada em `irlan-dev`;
PATCH a cada correção. Tags: `v55.x.y`.

## [Unreleased]

### Added
- Estrutura de desenvolvimento própria: Spec Kit (`.specify/`, skills `speckit-*`), constituição
  v1.0.0, `docs/VISAO.md`, `docs/demandas.md` (parecer das 14 demandas), `docs/HARNESS.md`,
  `docs/conversas/`.
- Análises `docs/llm-gestao.md` (Gemini grátis + DeepSeek + OpenAI por roteador, com classe
  sensível) e `docs/gestao-pessoal.md` (Notion como bancada, Obsidian como memória de 10 anos);
  roteiro refeito com as specs de gestão pessoal.
- Agentes do projeto por modelo (`lv-implementer` Sonnet, `lv-quick` Haiku, `lv-reviewer` Opus),
  skills `lv-validate`, `lv-new-skill` e `lv-triage`, hook de `py_compile` e hook de sessão na nuvem.
- Spec 001 — registro de falhas e medição de uso de LLM (tudo em `logs/`, local, sem segredos):
  - **Falhas:** o console vai também para `logs/jarvis.log`; falhas de ações, plugins, ferramentas,
    escada do Gemini, sessão de voz e exceções que escapam (`core/errlog.py`, `core/redact.py`) vão
    para `logs/failures.jsonl` com contexto, versão, assinatura (sem número de linha) e limite de
    rajada. A "falha mole" (a ação engoliu o erro e devolveu texto) também é registrada. Escada do
    Gemini esgotada ou sem rede vira uma linha no log da tela.
  - **Uso de LLM e custo** (`core/llm_usage.py`, `config/llm_prices.json`): cada chamada avulsa,
    turno de voz e amostra de voz grava tokens por modalidade, latência e o custo estimado no pago
    (mesmo no grátis). `tools/usage_report.py` resume por dia, modelo ou provedor.
  - **`report_problem`:** ferramenta de voz que anota "não funcionou" com a transcrição, as últimas
    ferramentas e as falhas recentes; e um **relato automático** quando a fala contém "não
    funcionou", "não deu certo" ou "deu errado" (`source: tool|auto` em `logs/reports.jsonl`).
  - Primeira base de testes (`tests/`, `requirements-dev.txt`) e a skill `lv-triage` atualizada com
    os formatos reais.
  - **Limites conhecidos:** SC-007 (relato com a expectativa certa) não atingido — o reconhecimento de
    voz do Gemini transcreve mal parte das frases (1 de 3 relatos com "eu queria X", e com X errado);
    SC-004 conferido com uma sessão real de 3,5 min e 1 chamada avulsa (não 10 min); SC-008 (plugin em
    loop por 10 min) não foi executado, só coberto por testes do limite de rajada.

### Changed
- Exceção que escapa de um slot do Qt (ou de qualquer thread) não fecha mais o JARVIS: o
  `sys.excepthook` próprio da spec 001 registra a falha em `logs/failures.jsonl`, imprime o
  traceback como antes e o app segue rodando. Antes, o PyQt6 abortava o processo. Decidido pelo
  Irlan em 2026-10-17.

## [55.1.0] — 2026-10-15

Trabalho do fork sobre o upstream até `a56cb2a`.

### Added
- HUD: estilos novos, motor de holograma com luz aditiva e backend GPU, superfície de malha na
  GPU, rig facial por landmarks, blendshapes e painel ✨ HOLOGRAMA ao vivo.
- 30 vozes com amostra; painel VOZ E JEITO; visão ao vivo pela webcam.
- `CLAUDE.md` e integração com graphify.

### Fixed
- Câmera em PiP, laço de instalação da palavra de ativação, renomear ao vivo.
- Padrões do `.gitignore` que deixavam a chave da API e a chave TLS fora da proteção.

## [55.0.0] — 2026-09-27

Base: upstream FatihMakes/Mark-LV em `c3e795a`.
