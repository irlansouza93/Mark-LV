# JARVIS — Mark LV (fork de irlansouza93)

Assistente pessoal de IA por voz em tempo real (Windows/macOS/Linux), em Python + PyQt6, sobre a **Gemini Live API** (áudio nativo bidirecional). Fork de `FatihMakes/Mark-LV`, mantido por Irlan. Responda ao usuário em **português (pt-BR)**; código, comentários e textos da UI ficam em inglês, como no projeto original.

## Leia antes de propor ou implementar
1. [`.specify/memory/constitution.md`](.specify/memory/constitution.md) — regras não negociáveis de **como** construir (vence este arquivo em conflito).
2. [`docs/VISAO.md`](docs/VISAO.md) — visão, decisões com data e roteiro. **Não re-discutir decisões registradas** sem o usuário pedir.
3. [`docs/demandas.md`](docs/demandas.md) — estado atual e parecer de cada demanda do roteiro.
4. [`docs/HARNESS.md`](docs/HARNESS.md) — como o Claude trabalha aqui: specs, hooks, skills, agentes por modelo.

O Mark LV é **independente do Mark LVI** (`C:\DESENVOLVIMENTO\JARVIS\Mark-LVI`): o LVI pode inspirar, mas trabalho do LV nunca edita, commita nem abre PR lá.

## Fluxo de trabalho
- `main` espelha o upstream e nunca recebe commit nosso. **`irlan-dev`** é a base de desenvolvimento.
- **Feature** = spec do Spec Kit: `/speckit-specify` (a partir de `irlan-dev` atualizada) cria a branch `NNN-nome` e `specs/NNN-nome/`; depois `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`. Manutenção sem spec: `fix/…`, `chore/…`, `docs/…`. Tudo volta por **PR para `irlan-dev`**.
- **Modelos:** especificar/planejar/revisar na sessão principal (modelo mais forte); tarefas já planejadas → agente `lv-implementer` (Sonnet); mudanças mecânicas → `lv-quick` (Haiku); revisão independente → `lv-reviewer`. Nenhum agente faz commit/push/PR.
- **Versão própria** `55.MINOR.PATCH` em [`CHANGELOG.md`](CHANGELOG.md) (seção "Unreleased" em todo PR); tags `v55.x.y`.
- **Memória do projeto** = decisões na `docs/VISAO.md` + resumos em `docs/conversas/`. Decisão nova tomada em conversa → registrar lá, com data.
- Remotes: `origin` = fork (`irlansouza93/Mark-LV`), `upstream` = `FatihMakes/Mark-LV`. Nunca dar push no upstream. Novidades do upstream: atualizar `main` e mesclar em `irlan-dev`.
- Ambiente: `.venv/` (Python 3.14). Rodar: `.venv/Scripts/python.exe main.py`. Instalar deps: `python setup.py`.
- Verificação: skill **`lv-validate`** (passo a passo). Em resumo:
  - `py_compile` em tudo que mudou (o hook `PostToolUse` já compila cada `.py` editado);
  - lógica sem interface: `pytest` em `tests/` (`pip install -r requirements-dev.txt` uma vez; `.venv/Scripts/python.exe -m pytest tests -q`, ~5 s, sem rede nem chave — Gemini, relógio e disco ficam atrás de funções que o teste troca);
  - UI/HUD: `tools/hud_bench.py --style <estilo> --out <png>` renderiza offscreen, salva o quadro e mede ms/quadro (alvo p95 < 15 ms — o HUD pinta a 20–30 Hz); olhe a imagem;
  - Gemini: chamadas reais usam a chave de `config/api_keys.json` e precisam rodar fora do sandbox.
- Erros e relatos do JARVIS → skill **`lv-triage`** para virar fix ou spec. Nova habilidade do JARVIS → skill **`lv-new-skill`**.
- **Economia de tokens** (regras em [`docs/HARNESS.md`](docs/HARNESS.md#economia-de-tokens-decidido-em-2026-10-19)): uma sessão por spec/tarefa; `graphify query` antes de ler arquivos; tarefas planejadas no `lv-implementer` (Sonnet), mecânicas no `lv-quick` (Haiku); saída de comandos curta; teste real em um único roteiro.

## Mapa do código
| Onde | O quê |
|---|---|
| `main.py` | `JarvisLive`: sessão Gemini Live, loops asyncio (mic, envio, recepção, playback), ferramentas "inline" (`screen_process`, `close_camera`, `live_camera`, memória, monitor, shutdown), reconexão/retomada de sessão |
| `ui.py` | Toda a interface Qt (~6 mil linhas). `HudCanvas` = centro do HUD; `MainWindow` = janela; `JarvisUI` = fachada **thread-safe** usada pelo `main.py` (sempre via `pyqtSignal`, nunca tocar widget fora da thread Qt) |
| `core/holo_engine.py`, `core/holo_gpu.py` | Motor de holograma: luz aditiva, bloom, scanlines, aberração. `make_buffer()` devolve o backend GPU (moderngl, offscreen) ou o numpy — mesma API (`points/lines/polyline/disc/compose`) |
| `core/holo_head.py` | `HoloHead`: a cabeça como holograma (partículas + superfície), guiada por `self.look`; herda animação/lip-sync de `HoloAvatar` |
| `core/holo_surface.py` | A malha real na GPU: modo holograma (depth pre-pass, fresnel, faixas, ruído) ou **realista** (personagem iluminado, opaco, MSAA, ACES). Rig no shader: mandíbula com costura afiada + cavidade/dentes, lábios por visema, globos oculares girando com o olhar, pálpebras desenhadas |
| `core/face_rig.py` | Rig facial por landmarks: MediaPipe num render frontal (uma vez por modelo, cache `*.face.npy`) → linha exata dos lábios (triângulos-ponte viram o interior da boca com dentes), pálpebras pelo contorno real, íris de olho pintado segue o olhar. Sem rosto detectado → só queixo suave (nunca boca falsa) |
| `core/blendshapes.py` | Morph targets do GLB (ARKit `jawOpen`/`eyeBlink*`, visemas Oculus/RPM, VRM `Fcl_*`, VRChat) mapeados para fala/piscar/olhar; quando existem, substituem o rig procedural |
| `core/holo_look.py` | O "look" de cada estilo (parâmetros, presets, anatomia calibrada) — editado ao vivo em ⚙ SETUP → ✨ HOLOGRAMA (`HoloLookPanel` em `ui.py`), salvo em `config/holo_looks.json` |
| `core/holo_viz.py` | Visualizadores de voz: `HoloOrb`, `HoloProjector` |
| `core/mesh_import.py` | Importa OBJ/GLB de `assets/avatars/` como estilo de HUD (`model:<nome>`): isola a cabeça, orienta, amostra, detecta anatomia (olhos/nariz/boca/queixo), monta a malha da GPU; cache em `config/avatar_cache/` (suba `_VERSION` ao mudar o processamento) |
| `core/avatar.py`, `core/avatar_mesh.py` | Animação/rig da cabeça e malha canônica do MediaPipe; o desenho chapado antigo fica só como fallback |
| `core/gemini.py` | "Escada" de modelos para chamadas one-shot (cooldown por tipo de falha, timeout obrigatório). Toda chamada one-shot ao Gemini passa por aqui |
| `core/voice_preview.py` | Amostras de voz para o seletor (TTS do Gemini, cache em `config/voice_cache/`) |
| `core/errlog.py` | Registro em `logs/`: uma thread de escrita recebe dicts por fila (quem chama gasta ~1 µs). `jarvis.log` (console, via tee de stdout/stderr), `failures.jsonl` (assinatura, agrupamento, falha "mole", limite de rajada), `reports.jsonl` (relatos), `usage-*.jsonl`. `setup()`/`bind()` ligados no `main.py`; ganchos `record_failure`/`note_tool` nos loaders, em `core/gemini.py` e no `main.py` |
| `core/redact.py` | Filtro de segredos aplicado a **todo** registro antes de ir ao disco (chaves de `api_keys.json`, formatos conhecidos, campos por nome) |
| `core/fs_policy.py` | Política central de pastas liberadas: `require(path, op, actor=)` antes de tocar disco, lista de negação fixa, `walk`/`confined`; lista salva em `allowed_folders` de `config/api_keys.json` |
| `core/llm_usage.py` | Uso de LLM e custo: tokens por modalidade, preço por tabela, medidor dos turnos de voz, resumo (`summarize`). `live_message(response)` roda em toda mensagem do Live — só lê atributos e enfileira |
| `core/wait_cue.py` | Rede de segurança do silêncio (spec 002): `WaitWatchdog` puro (relógio injetável) decide HUD "pensando", frase de espera e desistência a partir do fim da fala; frases na voz do JARVIS em cache (`config/voice_cache/wait_*`), tom de reserva. Ganchos no `main.py` (`_wait_cue_loop`, `_wait_call`) |
| `config/llm_prices.json` | Tabela de preços (US$/1M tokens, datada, com fonte); editar não exige código |
| `actions/*.py` | Habilidades embutidas: cada arquivo expõe um dict `TOOL` com `handler` e é auto-descoberto por `core/action_loader.py`. `report_problem.py` grava o relato "não funcionou"; o relato **automático** (fala com "não funcionou"/"não deu certo"/"deu errado") é feito pelo `errlog.end_turn()` |
| `plugins/*.py` | Plugins drop-in: dict `PLUGIN` + `run()` (ver `plugins/_template.py`), descobertos por `core/plugin_loader.py`, liga/desliga em 🧩 PLUGINS sem reiniciar |
| `memory/config_manager.py` | Toda leitura/escrita de `config/api_keys.json` (flags, voz, estilo de HUD, dispositivos) |
| `memory/memory_manager.py` | Memória de longo prazo (`memory/long_term.json`) + busca local `recall_memory` |
| `dashboard/` | Controle remoto pelo celular (FastAPI + TLS próprio) |
| `tools/` | Ferramentas de desenvolvimento do fork (não são do app): `hud_bench.py` mede e captura o HUD offscreen; `usage_report.py` resume o uso de LLM (`--days N`, `--by`, `--json`) |
| `tests/` | `pytest` da lógica sem interface (filtro de segredos, errlog, uso/custo, ganchos com SDK simulado). Nunca importam `main.py`/`ui.py`/`google.genai` nem usam rede |
| `specs/`, `.specify/`, `docs/` | Specs por feature, Spec Kit (templates, scripts, constituição) e documentação do fork |

## Convenções do projeto (siga ao editar)
- **Comentários explicam o porquê, com números medidos** ("medido em 2.1 s…"), no tom do código existente. Comente decisões, não o óbvio.
- **Multiplataforma sempre**: nada que assuma Windows sem ramo para macOS/Linux; deps só de Windows levam marcador `sys_platform`.
- **Dependência nova só com justificativa**, e importada dentro de `try/except` no ponto de uso para que a falta dela desligue só um recurso.
- **Nada bloqueia a thread Qt**: trabalho lento vai para thread/executor e volta por sinal.
- HUD: novos estilos implementam `step(dt, amp, speaking=, muted=, state=, **kw)` e `paint(p, cx, cy, r, primary, accent, bg, W, H)`, recebem as cores como argumento (o tema é ao vivo), desenham via `make_buffer()` (nunca polígono opaco — holograma é luz aditiva) e são registrados em `HUD_STYLE_INFO` (`memory/config_manager.py`) e `HudCanvas._renderer` (`ui.py`). Orçamento: < 15 ms/quadro em GPU no tamanho real da tela (r≈330 px).
- Textos de UI novos em pt-BR quando o usuário pedir explicitamente (ex.: painel VOZ E JEITO); o resto da UI segue em inglês até uma tradução completa.
- Nova habilidade embutida = um arquivo em `actions/` com `TOOL`. Toda declaração vai para o modelo em **toda** conexão (custa tokens): descrição curta e precisa.

## Armadilhas conhecidas
- **`.gitignore` não aceita comentário no fim da linha** — `padrão  # nota` vira um padrão literal que não casa com nada. Comentários sempre em linha própria (foi assim que a chave da API e a chave TLS deixaram de ser ignoradas).
- Todo caminho escolhido pelo modelo passa por `fs_policy.require` (nunca regra local nem `Path(...)` direto); rodar código escrito pelo modelo passa por `confirm.request(..., report=True)`.
- Segredos que nunca entram no git: `config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`, `memory/long_term.json`. Antes de commitar, confira `git status`.
- Webcam no Windows: se nada abre, primeiro olhar Configurações → Privacidade → Câmera (`actions/screen_processor.camera_problem()` diagnostica). A câmera tem **um único dono**: o stream da UI (`MainWindow._cam_loop`); quem precisar de quadro usa `ui.camera_frame()`, nunca abre a câmera de novo.
- O modelo Live **não** está na escada de `core/gemini.py` (modelos Live não são intercambiáveis); a voz é fixada na conexão — trocar voz reconstrói a sessão.
- Notebook com Optimus (o do Irlan: RTX 3050 + Intel UHD): o OpenGL cai na GPU integrada por padrão; `core/holo_gpu._context` define `SHIM_MCCOMPAT` para usar a NVIDIA. Confira `GL_RENDERER` antes de medir desempenho.
- O console do Windows pode não ser UTF-8: ao rodar scripts que imprimem emoji, use `PYTHONIOENCODING=utf-8`.
- **O console agora também vai para `logs/jarvis.log`** (o `errlog` troca `sys.stdout`/`sys.stderr` por um tee): `print` novo já fica registrado, filtrado de segredos. Para erro de verdade, use `errlog.record_failure`.
- Tudo o que o JARVIS registra — inclusive a **transcrição das falas nos relatos** — fica em `logs/`, legível e apagável à mão (Princípio VIII). No Windows só dá para apagar com o app fechado (o app mantém os arquivos abertos).
- **`main.py` (e outros arquivos do upstream) são guardados com CRLF no git.** Algumas ferramentas de edição convertem o arquivo inteiro para LF e o diff vira uma reescrita do arquivo todo. Depois de editar, confira `git diff --stat` (só as linhas novas) e, se preciso, restaure o CRLF.
- `shutdown_jarvis` termina com `os._exit(0)`: **pula `finally` e `atexit`**. Nada que precise ser gravado ao sair pode depender deles (foi por isso que sessões de voz ficaram sem registro de fim; o resumo estima a duração).
- Um worktree não tem `config/api_keys.json` (fica fora do git): copie o do repositório principal para rodar o app nele.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
