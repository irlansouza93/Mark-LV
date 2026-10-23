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
- Wait safety net (spec 002): when the model stays silent after the user speaks, the HUD shows the thinking state (1.5 s), JARVIS says a short cached "one moment" phrase (4 s, once per request, rotating among 5 pt-BR variants) captured once from the Live model itself in the session's voice and checked against its transcript (TTS with the BCP-47 speech language as fallback, a short tone as last resort); the phrase always finishes before the answer, with a 0.2 s pause, and the mic is gated while it plays so it never reaches the model as user speech; and, after 20 s with no reply and no tool running, a give-up phrase plus a soft failure in logs/. Every request records its latency (`voice_wait`) in the usage log; `tools/usage_report.py` shows p50/p90/p95 with and without tools and suggests new limits. Limits live in `config/api_keys.json` → `wait_cue`.

### Changed
- Exceção que escapa de um slot do Qt (ou de qualquer thread) não fecha mais o JARVIS: o
  `sys.excepthook` próprio da spec 001 registra a falha em `logs/failures.jsonl`, imprime o
  traceback como antes e o app segue rodando. Antes, o PyQt6 abortava o processo. Decidido pelo
  Irlan em 2026-10-17.

### Fixed
- **Sessão de voz caía a cada ~10 min (GoAway)**: o servidor do Gemini Live avisa com `go_away`
  (e `time_left`) antes de fechar a conexão no limite de tempo; o app ignorava o aviso e era
  derrubado com `1008 (policy violation)` (56 vezes em 2026-10-19). Agora renova a conexão com o
  handle de retomada no primeiro momento de silêncio (sem áudio do modelo, sem fala do usuário e
  sem resposta devida a uma pergunta) e, em qualquer caso, 3 s antes do prazo; o fim fica
  registrado como `go_away` no uso de LLM. No modo wake-word, quem estava acordado continua
  acordado depois da renovação.
  As ferramentas passam a rodar fora do laço de recepção (antes ele ficava cego durante uma
  pesquisa de 14 s+ e não via o aviso). Efeito visível: uma ferramenta `NON_BLOCKING` agora deixa
  o modelo falar enquanto roda, e dois pedidos seguidos podem rodar ao mesmo tempo. Um resultado
  cuja conexão acabou — ou cuja resposta falada foi cortada — é entregue como texto, delimitado
  e marcado como dado, à sessão nova, para o usuário ouvir a resposta; a captura de
  `screen_process` não atravessa a reconexão (o modelo avisa e oferece olhar de novo, e a câmera
  aberta por ela é fechada). Ferramenta que levanta exceção agora devolve um erro ao modelo em vez
  de deixá-lo esperando (`core/live_session.py`: `go_away_should_reconnect`, `ResultMailbox`,
  `orphan_prompt`). Para testar sem esperar 10 min: `JARVIS_TEST_GOAWAY=40,20` simula o aviso
  aos 40 s com 20 s de prazo, e `JARVIS_TEST_GOAWAY=tool,20` simula o aviso quando a próxima
  ferramenta começa.

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
