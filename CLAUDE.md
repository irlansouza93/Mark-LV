# JARVIS — Mark LV (fork de irlansouza93)

Assistente pessoal de IA por voz em tempo real (Windows/macOS/Linux), em Python + PyQt6, sobre a **Gemini Live API** (áudio nativo bidirecional). Fork de `FatihMakes/Mark-LV`, mantido por Irlan. Responda ao usuário em **português (pt-BR)**; código, comentários e textos da UI ficam em inglês, como no projeto original.

## Fluxo de trabalho
- Desenvolvimento na branch **`irlan-dev`**; `main` acompanha o upstream. Não commitar direto na `main`.
- Remotes: `origin` = fork (`irlansouza93/Mark-LV`), `upstream` = `FatihMakes/Mark-LV`. Nunca dar push no upstream.
- Ambiente: `.venv/` (Python 3.14). Rodar: `.venv/Scripts/python.exe main.py`. Instalar deps: `python setup.py`.
- Não há suíte de testes. Valide assim:
  - `.venv/Scripts/python.exe -m py_compile <arquivos>` em tudo que mudou;
  - UI/HUD: renderizar offscreen com `QT_QPA_PLATFORM=offscreen`, instanciar `ui.HudCanvas` / `ui.JarvisUI`, chamar `grab()` e olhar a imagem; medir ms/quadro (alvo < 15 ms — o HUD pinta a 20–30 Hz);
  - Gemini: chamadas reais usam a chave de `config/api_keys.json` e precisam rodar fora do sandbox.

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
| `actions/*.py` | Habilidades embutidas: cada arquivo expõe um dict `TOOL` com `handler` e é auto-descoberto por `core/action_loader.py` |
| `plugins/*.py` | Plugins drop-in: dict `PLUGIN` + `run()` (ver `plugins/_template.py`), descobertos por `core/plugin_loader.py`, liga/desliga em 🧩 PLUGINS sem reiniciar |
| `memory/config_manager.py` | Toda leitura/escrita de `config/api_keys.json` (flags, voz, estilo de HUD, dispositivos) |
| `memory/memory_manager.py` | Memória de longo prazo (`memory/long_term.json`) + busca local `recall_memory` |
| `dashboard/` | Controle remoto pelo celular (FastAPI + TLS próprio) |

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
- Segredos que nunca entram no git: `config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`, `memory/long_term.json`. Antes de commitar, confira `git status`.
- Webcam no Windows: se nada abre, primeiro olhar Configurações → Privacidade → Câmera (`actions/screen_processor.camera_problem()` diagnostica). A câmera tem **um único dono**: o stream da UI (`MainWindow._cam_loop`); quem precisar de quadro usa `ui.camera_frame()`, nunca abre a câmera de novo.
- O modelo Live **não** está na escada de `core/gemini.py` (modelos Live não são intercambiáveis); a voz é fixada na conexão — trocar voz reconstrói a sessão.
- Notebook com Optimus (o do Irlan: RTX 3050 + Intel UHD): o OpenGL cai na GPU integrada por padrão; `core/holo_gpu._context` define `SHIM_MCCOMPAT` para usar a NVIDIA. Confira `GL_RENDERER` antes de medir desempenho.
- O console do Windows pode não ser UTF-8: ao rodar scripts que imprimem emoji, use `PYTHONIOENCODING=utf-8`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
