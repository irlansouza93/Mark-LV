---
name: lv-validate
description: Verify a Mark LV change before calling it done — py_compile on changed files, pytest when tests exist, offscreen HUD render with ms/frame for UI work, and the real-Gemini check list. Use after implementing any task, fix or feature in this repo.
---

# Validar uma mudança no Mark LV

O upstream não tem suíte de testes; esta é a verificação que substitui "rodar os testes". Rode
tudo o que se aplica à mudança e **relate o resultado de cada passo** (comando + saída curta). O
que não puder ser verificado deve ser dito explicitamente.

Interpretador: `.venv/Scripts/python.exe` (Windows) ou `.venv/bin/python` (macOS/Linux) — abaixo
`$PY`. Em consoles Windows, prefixe `PYTHONIOENCODING=utf-8`.

## 1. Sintaxe (sempre)
```bash
git diff --name-only irlan-dev...HEAD -- '*.py'; git diff --name-only -- '*.py'
$PY -m py_compile <cada arquivo listado>
```
(O hook `PostToolUse` já compila cada `.py` editado; este passo pega o que mudou por outros meios.)

## 2. Testes de lógica (quando existir `tests/`)
```bash
$PY -m pytest -q
```
Lógica nova sem teste viola a constituição (Princípio II): escreva o teste antes de seguir.

## 3. HUD / interface (se tocou em `ui.py`, `core/holo_*`, `core/face_rig.py`, `core/blendshapes.py`, `core/mesh_import.py`, `core/holo_viz.py`)
```bash
$PY tools/hud_bench.py --style <estilo> --size 700 --frames 60 --out <scratchpad>/hud.png
$PY tools/hud_bench.py --style <estilo> --speaking --out <scratchpad>/hud_speaking.png
```
- Abra a imagem e confira o que mudou (não basta o script rodar).
- Orçamento: p95 < 15 ms. Estilos: `face`, `orb`, `holo`, `core`, `model:<nome>`.
- Número de GPU só vale com `GL_RENDERER` da NVIDIA no log (notebook Optimus).
- Painéis e overlays: instancie o widget offscreen, `resize()`, `grab().save(...)` e olhe.

## 4. Comportamento com o Gemini (se mudou prompt, declaração de ferramenta ou fluxo da sessão)
Não dá para testar no sandbox: precisa da chave de `config/api_keys.json` e rede. Liste no PR o
roteiro de falas a testar (o que dizer e o que deve acontecer) e peça ao Irlan para rodar
`$PY main.py`, ou rode fora do sandbox se ele autorizar. Mudou declaração de ferramenta? Informe
quantos caracteres ela soma a cada conexão.

## 5. Antes do commit
- `git status`: nenhum arquivo de `config/api_keys.json`, `config/certs/`, `memory/long_term.json`,
  tokens OAuth.
- `CHANGELOG.md` → "Unreleased" atualizado.
- `graphify update .`
