# Quickstart — validar o aviso de espera garantido

## 1. Verificação automática (sem rede, sem chave)

```bash
.venv/Scripts/python.exe -m py_compile main.py core/wait_cue.py core/voice_preview.py core/llm_usage.py memory/config_manager.py tools/usage_report.py
```

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

Esperado: tudo verde, incluindo `test_wait_cue.py`, `test_wait_summary.py`, `test_wait_config.py`
e a seção nova de `test_usage_report.py`. O HUD não muda (reusa o estado `THINKING`), então não
há `hud_bench` nesta feature.

## 2. Teste real — um único roteiro (fora do sandbox, com a chave)

Preparação: apagar `config/voice_cache/wait_*` (para ver o tom e a geração da frase), conferir
que `config/api_keys.json` não tem `wait_cue` (padrões 1,5 / 4 / 20 s) e anotar a hora de início.

```bash
.venv/Scripts/python.exe main.py
```

Falar em sequência, numa sessão só, anotando o que se vê/ouve:

| # | O que fazer | Esperado |
|---|---|---|
| 1 | Logo ao conectar: "Que horas são?" | resposta rápida; HUD **não** pisca "pensando" (SC-002) |
| 2 | "Pesquise na web as notícias de hoje sobre inteligência artificial e me resuma." | algum sinal até ~1,7 s; se o modelo não avisar, HUD "pensando" e, aos 4 s, **tom** (cache ainda vazio na 1ª vez) ou a frase; nada por cima da voz (SC-001, SC-003) |
| 3 | Conferir `config/voice_cache/wait_hold_<voz>_pt.wav` e `wait_giveup_…` | criados em segundo plano depois da conexão |
| 4 | "Abra o Steam." (≈4,7 s medido na spec 001) | se não houver aviso falado: "Um momento." **na voz do JARVIS**, uma vez só, avatar articulando |
| 5 | Falar com outra pessoa, sem chamar o JARVIS: "Pode me passar a água?" | nenhuma frase; registro `no_reply` (ver R2: se vier `gave_up`, aplicar a contingência antes do merge) |
| 6 | "Lembre que meu café favorito é espresso." (ferramenta silenciosa) | nenhuma frase depois que o turno fecha |
| 7 | Fazer um pedido longo e silenciar o microfone logo depois | espera cancelada, sem frase |
| 8 | Fazer um pedido longo e interromper (falar por cima) | nada da frase sobra; o turno seguinte mede do zero |
| 9 | Digitar na interface: "Qual a previsão do tempo para amanhã?" | mesma rede vale para texto (registro `source: text`) |
| 10 | Encerrar com "Pode desligar." | — |

## 3. Conferir os registros

```bash
.venv/Scripts/python.exe tools/usage_report.py --days 1
```

- Seção "Espera da voz" presente, com p50/p90/p95 por tipo e "dados insuficientes (N de 30)".
- `logs/usage-AAAA-MM.jsonl`: um `voice_wait` por pedido; ≥ 95% dos pedidos respondidos com
  `latency_ms` (SC-004).
- Tokens de texto por `live_turn` na mesma faixa de antes (16,4–17,1 mil, spec 001) (SC-005).
- `logs/failures.jsonl`: nenhuma falha nova, salvo `wait_give_up` se uma desistência aconteceu.

A desistência (20 s sem nada) não se provoca de propósito com o servidor normal: ela é coberta
pelo `pytest`; no roteiro real, registrar se ocorreu. Registrar no PR o resultado de cada linha
da tabela e o que não pôde ser verificado (Princípio II).
