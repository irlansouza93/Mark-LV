# Quickstart — validar a feature 001

Roteiro para provar que a feature funciona de ponta a ponta. Detalhes de formato em
[data-model.md](data-model.md); interfaces em [contracts/](contracts/).

## 0. Pré-requisitos

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

## 1. Lógica pura (sem rede, sem chave) — SC-002, SC-003, SC-009

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

Esperado: tudo verde em < 10 s. Cobertura mínima: filtro de segredos (chaves plantadas em
mensagem, dict aninhado, args e traceback), assinatura estável entre linhas diferentes, 5 falhas
iguais → 1 grupo de 5, soft-failure, versão (pasta `.git`, arquivo `gitdir:`, `packed-refs`, sem
git), retenção de 12 meses, supressão de rajada, custo (texto, áudio, modelo sem preço, tokens
desconhecidos), medidor do Live com mensagens falsas, resumo diário, horário de volta da cota nas
viradas do horário de verão dos EUA; ganchos da escada (`core/gemini.py` com cliente falso) e
dos loaders (ação/plugin de teste que lançam exceção) gravando no `tmp_path`.

## 2. Compilação dos arquivos tocados

```bash
.venv/Scripts/python.exe -m py_compile main.py core/errlog.py core/redact.py core/llm_usage.py core/gemini.py core/action_loader.py core/plugin_loader.py core/voice_preview.py actions/report_problem.py tools/usage_report.py
```

## 3. Custo do registro na thread de quem chama — SC-006

Microbenchmark (script de bancada da implementação, fora dos testes para não ser instável):
10 000 chamadas de `errlog.record_failure` com exceção real e de `llm_usage.record_oneshot`;
esperado p95 < 1 ms por chamada. Depois, `tools/hud_bench.py` com o app registrando em paralelo:
p95 < 15 ms/quadro (GPU confirmada por `GL_RENDERER`).

## 4. Falhas provocadas no app real — SC-001, SC-008

Rodar `.venv/Scripts/python.exe main.py` (fora do sandbox, chave real) e provocar:
1. uma ação que quebra: plugin de teste em `plugins/` que lança exceção no `run()` (e outro que
   falha no import);
2. uma falha mole: pedir para abrir um arquivo inexistente;
3. cota na escada: já coberta sem rede por `tests/test_gemini_hooks.py` (cliente falso que
   responde 429 em todo degrau → um registro por degrau, um `ladder_exhausted` e **uma** chamada
   ao notificador em duas tentativas seguidas). No app real basta conferir, se acontecer
   naturalmente, a linha `SYS: Gemini free quota used up …` no log da tela;
4. queda da voz: desligar a rede por ~10 s com a sessão aberta.
Fechar o app e conferir:

```bash
.venv/Scripts/python.exe -c "import json,collections;r=[json.loads(l) for l in open('logs/failures.jsonl',encoding='utf-8')];print(collections.Counter((x['component'],x['error_type']) for x in r))"
```

Esperado: cada falha provocada presente, com `app`, `signature`, `origin`; repetir o item 1
cinco vezes dá a mesma assinatura. Plugin falhando em loop por 10 min: `failures.jsonl` + rotação
≤ 25 MB e registros com `suppressed`.

## 5. Segredos — SC-003

Com o app parado, depois do passo 4:

```bash
.venv/Scripts/python.exe -c "import json,pathlib;k=json.load(open('config/api_keys.json'))['gemini_api_key'];print(sum(k in p.read_text(encoding='utf-8',errors='ignore') for p in pathlib.Path('logs').glob('*')))"
```

Esperado: `0`.

## 6. Uso de LLM — SC-004, SC-005

1. Diagnóstico do Live (uma vez, research R7): `JARVIS_USAGE_RAW=1` + 2–3 turnos de voz; conferir
   em `logs/usage-raw.jsonl` se `usage_metadata` é por turno ou acumulado e ajustar o medidor.
2. Sessão real de 10 minutos de voz com 3–4 chamadas avulsas (screenshot, pesquisa).
3. `tools/usage_report.py --days 1` → ≥ 95% dos usos com tokens; conferir à mão o custo de 2
   linhas pela tabela (diferença < 1%).
4. `tools/usage_report.py --days 14` sobre registros sintéticos de 14 dias (gerador da bancada)
   termina em < 5 s.

## 7. `report_problem` — SC-007

Roteiro de 5 casos em [contracts/report_problem-tool.md](contracts/report_problem-tool.md):
o relato aparece em `logs/reports.jsonl` com `expected`, `transcript` (≤ 6 linhas) e
`recent_tools` em pelo menos 4 de 5 tentativas do caso 1; casos 4 e 5 não registram nada.

## 8. Fechamento

`graphify update .`, `CHANGELOG.md` (Unreleased → Added), PR para `irlan-dev` citando o que foi
e o que não foi verificado (Princípio II).
