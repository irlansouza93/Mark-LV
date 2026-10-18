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

### Fixed
Bugs do código do upstream vistos nos logs reais da validação da spec 001 (2026-10-17):
- `open_app` dizia "Opened C:\…\arquivo.txt." para um arquivo que não existe (o `:` do drive caía
  no ramo `start <nome>`, que volta antes do Windows mostrar o erro). Caminho absoluto inexistente
  agora responde "The file … does not exist." sem tentar abrir.
- Reconexão (`main.py`): uma queda de rede no meio da sessão chega como ExceptionGroup do
  TaskGroup; o teste de "handle de retomada rejeitado" casava "handle" com "un**handle**d errors in
  a TaskGroup", jogava fora a retomada e reconectava sem espera. As decisões agora olham as
  exceções de dentro do grupo (`core/live_session.py`): queda de rede (ConnectionResetError /
  WinError 64, APIError 1006) mantém o handle e usa o backoff de rede; cota e chave inválida no
  meio da sessão também passam a ser reconhecidas.
- `QueueFull` no loop asyncio durante a queda: o envio fica preso no socket morto enquanto o
  microfone enche a fila de 200 blocos (~12,8 s). O bloco excedente é descartado, como o relay do
  celular já fazia.
- `browser_control`: nomes de ação que o modelo inventa (`open_tab`, `navigate`, `type_text`…) são
  mapeados para os reais; ação desconhecida responde com a lista válida sem abrir o navegador de
  automação. `type`/`fill_form` com seletor `input[…]` também aceitam `<textarea>` (a busca do
  Google é textarea desde 2023) e esperam no máximo 5 s, em vez de travar 30 s no `Locator.clear`.

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
