---
description: "Task list — 001 Registro de falhas e medição de uso de LLM"
---

# Tasks: Registro de falhas e medição de uso de LLM

**Input**: Design documents from `specs/001-error-log-llm-usage/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: OBRIGATÓRIOS — a spec (FR-020) e a constituição (Princípio II) pedem `pytest` da lógica
sem interface, **escrito antes e visto falhando**. Em cada fase, as tarefas de teste vêm antes das
de código; rode o teste, veja falhar, implemente, veja passar.

**Quem executa**: tarefas de código → agente `lv-implementer` (Sonnet); tarefas marcadas
**[real]** precisam do app rodando com a chave real, fora do sandbox, e da voz do Irlan → sessão
principal com o Irlan. Nenhum agente faz commit/push/PR.

**Regras para todas as tarefas** (repetidas aqui porque cada tarefa deve rodar sem outro contexto):
- Funções públicas de registro **nunca levantam exceção** e só põem um `dict` na fila (< 1 ms).
- Ganchos em arquivos do upstream (`main.py`, `core/*.py` existentes, `memory/config_manager.py`)
  são **uma linha** (ou o mínimo possível), sem reformatar nem reordenar nada ao redor; lista
  completa em [contracts/modules.md](contracts/modules.md#ganchos-nos-arquivos-existentes-o-diff-inteiro-do-upstream-é-isto).
- `ui.py` e as ações existentes em `actions/` **não** mudam.
- Código, comentários e textos em inglês; comentários explicam o porquê, com números medidos.
- Testes nunca importam `main.py`/`ui.py`/`google.genai` nem usam rede; SDK simulado com
  `types.SimpleNamespace`; pasta de logs = `tmp_path`.
- Depois de editar `.py`: `py_compile` (o hook já faz) e `pytest tests -q`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: pode rodar em paralelo (arquivos diferentes, sem dependência pendente)
- **[Story]**: US1 (falhas), US2 (uso de LLM), US3 (`report_problem`)

---

## Phase 1: Setup

**Purpose**: base de testes do projeto (não existe nenhuma hoje)

- [x] T001 Criar `requirements-dev.txt` na raiz com `pytest>=8,<10` e um comentário dizendo que é só para desenvolvimento (o app não depende dele); instalar no `.venv` com `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`
- [x] T002 Criar `tests/__init__.py` (vazio) e `tests/conftest.py` que insere a raiz do repositório no `sys.path` e oferece a fixture `log_dir` (= `tmp_path / "logs"`); confirmar que `.venv/Scripts/python.exe -m pytest tests -q` roda (0 testes, sem erro)

---

## Phase 2: Foundational (bloqueia todas as histórias)

**Purpose**: filtro de segredos, fila + thread de escrita, versão, retenção, log de texto — usados
por US1, US2 e US3.

**⚠️ CRITICAL**: nenhuma história começa antes desta fase.

### Testes (escrever antes; ver falhar)

- [x] T003 [P] Escrever `tests/test_redact.py` para `core/redact.py` (research R4): mascara valor literal de segredo passado em `secrets` dentro de str, dict aninhado, lista e texto de traceback; mascara formatos `AIza` + 35 chars, `sk-…`/`sk-proj-…`/`sk-ant-…`, token de bot Telegram `\d{8,10}:[A-Za-z0-9_-]{35}`, `Bearer <token>`, `Authorization: …`, `?key=`/`&token=`/`access_token=` em URL; mascara o valor inteiro de chaves de dict cujo nome contém `key`, `token`, `secret`, `password`, `authorization`, `cookie`; formato da máscara `‹redacted:AIza…f3›` (4 primeiros + 2 últimos); não altera texto sem segredo; não muta a entrada; `load_config_secrets()` devolve só valores-string com 16+ caracteres de um JSON de exemplo em `tmp_path`
- [x] T004 [P] Escrever `tests/test_errlog_pure.py` (parte fundacional) para funções puras de `core/errlog.py`: `parse_version()` com pasta `.git` + ref solta, arquivo `gitdir:` (worktree), ref só em `packed-refs`, HEAD destacado (hash direto), sem git → `"55.1.0+nogit"`, sem nada → `"unknown"`, versão lida do primeiro cabeçalho `## [55.x.y]` do CHANGELOG (ignorando `## [Unreleased]`), formato `55.1.0+8f9b865`; `months_to_delete()` com nomes `usage-YYYY-MM.jsonl` e `today` fixo apaga só meses com mais de 12 meses e ignora outros arquivos; `BurstLimiter` com relógio injetado: até 20 registros da mesma assinatura em 60 s passam, os seguintes são contados e `drain_suppressed(now)` devolve `{signature: N}` uma vez por minuto; `quota_reset_local()` devolve a próxima meia-noite do Pacífico convertida para um fuso fixo UTC−3, conferida antes/depois das viradas de horário de verão dos EUA (2º domingo de março 02:00, 1º domingo de novembro 02:00) e funcionando sem `zoneinfo` do Pacífico (research R11)
- [x] T005 Escrever `tests/test_errlog_writer.py` para a infraestrutura de escrita de `core/errlog.py` com `setup(log_dir, tee_console=False)`: um registro enfileirado aparece em `log_dir` depois de `flush()` como uma linha JSON com os campos comuns `v`=1, `ts` ISO com fuso, `app`, `pid`, `live_session`; o filtro de segredos é aplicado na escrita; textos livres cortados em 500 chars (`traceback` em 8 000) com sufixo `…[+N chars]`; dado não serializável vira texto em vez de exceção; `setup()` duas vezes não duplica handlers; diretório sem permissão/inexistente não levanta (registro descartado + aviso único); fila acima de 10 000 itens descarta e o próximo registro gravado leva `dropped: N`; `setup()` apaga `usage-*.jsonl` com mais de 12 meses

### Implementação

- [x] T006 Implementar `core/redact.py` conforme [contracts/modules.md](contracts/modules.md) e research R4 (`redact`, `mask`, `load_config_secrets`), só biblioteca padrão, regex pré-compiladas no módulo; T003 deve passar
- [x] T007 Implementar em `core/errlog.py` as funções puras `parse_version`, `months_to_delete`, `BurstLimiter` e `quota_reset_local` (zoneinfo quando disponível; senão regra de horário de verão dos EUA em código, UTC−7/−8) conforme research R6, R10, R11; T004 deve passar
- [x] T008 Implementar em `core/errlog.py` a infraestrutura: `setup(log_dir=ROOT/"logs", *, tee_console=True)` idempotente; `queue.SimpleQueue` + **uma** thread daemon de escrita que formata traceback, aplica `redact` (segredos de `config/api_keys.json` via `load_config_secrets`, relidos quando o mtime muda), corta textos, serializa e grava; arquivos e limites da research R10 (`failures.jsonl` 5 MB × 4, `reports.jsonl` 1 MB × 3, `usage-YYYY-MM.jsonl` por mês) com `logging.handlers.RotatingFileHandler` ou equivalente; função interna `_enqueue(stream, record)` usada por `record_*` de errlog e de llm_usage; campos comuns do data-model; contador `dropped` (fila > 10 000); `flush(timeout_s)` + `atexit` com prazo de 1 s; `version()` lida uma vez; `set_live_session(id)`; se a thread morrer, `record_*` vira no-op com aviso único; quando gravar falhar (disco cheio, sem permissão, arquivo travado) o aviso é **único** por processo e vai para o console (`print` no stream original, sem passar pelo tee) **e** para o log da tela pelo `notify` de `bind()` se já estiver ligado (spec, Edge Cases); limpeza de meses antigos no `setup()`. T005 deve passar
- [x] T009 **Primeiro** acrescentar a `tests/test_errlog_writer.py` um teste do tee com streams falsos (`io.StringIO`) — o texto chega ao stream original igual e a `logs/jarvis.log` filtrado; um stream que levanta `UnicodeEncodeError` não propaga — e vê-lo falhar; depois implementar em `core/errlog.py` o log de texto e a captura do console (research R3): `logs/jarvis.log` com `RotatingFileHandler` 2 MB × 5 por `QueueHandler`/`QueueListener`; tee de `sys.stdout`/`sys.stderr` (quando `tee_console=True`) que escreve no stream original exatamente como antes e põe a linha (filtrada) na fila — nunca levanta, nem com erro de encoding do console do Windows; `sys.excepthook` e `threading.excepthook` que registram `kind: "uncaught"`, `component: "thread"`/`"app"` e chamam o hook anterior
- [x] T010 Ligar em `main.py`: `from core import errlog` nos imports e `errlog.setup()` como primeira linha de `main()` (antes de `JarvisUI(...)`); `errlog.bind(notify=self.ui.write_log, transcript=lambda: list(self._session_log[-6:]))` em `JarvisLive.run()` logo depois de `set_trim_notifier(self.ui.write_log)`; implementar `bind(*, notify=None, transcript=None)` em `core/errlog.py`. Conferir com `py_compile main.py`

**Checkpoint**: `pytest tests -q` verde; rodar o app cria `logs/jarvis.log` com o console.

---

## Phase 3: User Story 1 — Falhas guardadas, agrupadas e sem segredos (Priority: P1) 🎯 MVP

**Goal**: toda falha de ação, plugin, ferramenta inline, escada do Gemini e sessão de voz vai para
`logs/failures.jsonl` com contexto, versão e assinatura; repetições agrupam; escada esgotada vira
uma linha no log da tela.

**Independent Test**: quickstart §1 (testes de US1) + §4 e §5 (falhas provocadas no app real e
busca da chave nos logs = 0).

### Testes (escrever antes; ver falhar)

- [x] T011 [P] [US1] Acrescentar a `tests/test_errlog_pure.py`: `signature()` = 12 hex de `sha1(f"{component}:{name}|{error_type}|{origin}")`, estável quando só a linha muda; `innermost_project_frame()` com traceback real de função do teste e de chamada que passa por stdlib/site-packages devolve o frame mais interno dentro da raiz do projeto como `("caminho/relativo.py:função", linha)`; `classify_soft_failure()` → `"soft:could not save"` para `"Could not save: [Errno 13] C:\\x\\y.txt"`, reconhece `Could not …`, `Unable to …`, `… failed: …`, `… error: …`, `Error …`, e devolve `None` para `"Done."`, `"Opened Excel"`, `"Tool 'x' failed: …"` e `"Sir, the 'x' plugin failed: …"` (crash do loader já registrado — research R1); `group()` com 5 falhas iguais + 2 de outra assinatura → 2 grupos ordenados por contagem, com `count`, `first_ts`, `last_ts`, `versions`, `example`
- [x] T012 [P] [US1] Escrever `tests/test_loader_hooks.py`: com `errlog.setup(log_dir, tee_console=False)`, uma pasta temporária de ações com um arquivo cujo handler lança `ValueError` e outro que falha no import → `discover_actions()` + `run()` geram registros `kind: "exception"`, `component: "action"`, `phase: "load"`/`"run"`, `context.args` filtrado, traceback e `origin` no arquivo de teste; o texto devolvido por `run()` continua `"Tool '<nome>' failed: …"`; o mesmo para plugins com `discover_plugins()`/`PluginRegistry.run()` (monkeypatch de `get_plugin_enabled` para `True`, sem ler config real)
- [x] T013 [P] [US1] Escrever `tests/test_gemini_hooks.py` (parte de falhas): monkeypatch de `core.gemini.client` e `core.gemini._live_call` para levantar `Exception("429 RESOURCE_EXHAUSTED")` em todo degrau e de `api_key` para `"AIza" + "x"*35`; `gemini.call("oi", tier=gemini.FAST)` devolve `None`, grava um registro `component: "gemini"`, `error_type: "quota"` por degrau tentado com `context.tier`, `timeout_ms`, `latency_ms`, e um `error_type: "ladder_exhausted"` com `context.tried` e `context.reasons`; `errlog.bind(notify=fake)` recebe **uma** linha começando por `"SYS: Gemini free quota used up"` mesmo chamando `call()` duas vezes seguidas; com `"503 UNAVAILABLE"` a linha é a de modelos fora do ar; a chave nunca aparece nos arquivos; limpar `_cooldown` entre testes

### Implementação

- [x] T014 [US1] Implementar em `core/errlog.py`: `signature`, `innermost_project_frame`, `classify_soft_failure`, `group` (puras) e `record_failure(component, name, *, exc=None, error_type=None, message="", phase=None, context=None, origin=None)` que monta o registro `Failure` do [data-model](data-model.md#failure--logsfailuresjsonl) (`kind` ∈ `exception|soft|gemini|live|uncaught|logger`; `component` ∈ `action|plugin|inline_tool|tool|gemini|live|thread|app`; traceback e origem calculados na thread de escrita) e aplica `BurstLimiter` (`suppressed`); T011 deve passar
- [x] T015 [US1] Implementar em `core/errlog.py` `note_tool(name, args, result)`: guarda no anel das últimas 8 ferramentas `{ts, name, args, result, soft_failure}` (args e result cortados) e, se `classify_soft_failure(str(result))` não for `None`, grava `kind: "soft"`, `component` = `"tool"` com `name`, `context: {args, result}`, `origin: "main.py:_execute_tool"`; e `notify_once(key, message, every_s=300)` (chama o `notify` ligado no máximo uma vez por `every_s` por chave; sem `notify` ligado, só imprime)
- [x] T016 [P] [US1] Ganchos em `core/action_loader.py`: no `except` de `ActionRegistry.run()` → `errlog.record_failure("action", name, exc=e, phase="run", context={"args": parameters})`; no `except` de `discover_actions()` → `errlog.record_failure("action", path.stem, exc=e, phase="load")`; import local ou no topo de `from core import errlog`; nada mais muda (mensagens, prints e retornos iguais); T012 (ações) deve passar
- [x] T017 [P] [US1] Ganchos em `core/plugin_loader.py`: no `except` de `PluginRegistry.run()` → `errlog.record_failure("plugin", name, exc=e, phase="run", context={"args": parameters})`; no `except` de `discover_plugins()` → `errlog.record_failure("plugin", path.stem, exc=e, phase="load", message=rec.error)` (depois de montar `rec`); nada mais muda; T012 (plugins) deve passar
- [x] T018 [US1] Ganchos de falha em `core/gemini.py` `call()`: medir `t0 = time.monotonic()` por degrau; em cada ramo do `except` (quota/gone/unavailable/outro, e `asyncio.TimeoutError`/"timed out" → `timeout`) chamar `errlog.record_failure("gemini", model, error_type=<motivo>, message=msg, context={"tier": tier, "timeout_ms": timeout_ms, "latency_ms": …})` sem mudar os `print`/`_cool` atuais; antes do `return None` final, `errlog.record_failure("gemini", "ladder", error_type="ladder_exhausted", context={"tier": tier, "tried": [...], "reasons": {...}})` e `errlog.notify_once("ladder", <texto>, every_s=_COOLDOWN_SECONDS)` com o texto da research R12 (`SYS: Gemini free quota used up on N models — back at HH:MM.` usando `errlog.quota_reset_local`, ou `SYS: Gemini models not answering (N tried) — try again shortly.` quando a maioria não é cota); também no ramo "no Gemini API key is configured"; T013 deve passar
- [x] T019 [US1] Ganchos em `main.py` `_execute_tool`: no `except Exception as e:` final → `errlog.record_failure("inline_tool", name, exc=e, phase="run", context={"args": args})` (antes do `traceback.print_exc()`); logo antes de `print(f"[JARVIS] 📤 {name} → …")` → `errlog.note_tool(name, args, result)`; uma linha cada
- [x] T020 [US1] Ganchos em `main.py` `run()` (loop de conexão): depois de `print("[JARVIS] Connected.")` → `errlog.set_live_session(<id curto novo, ex.: uuid4().hex[:8]>)`; no `except BaseException as e:`, **depois** do bloco `if _is_reconnect_signal(e): … continue` (reconexão voluntária não é falha) → `errlog.record_failure("live", "live_connect", exc=e, phase="resume" if _resumed_with else "connect", context={"model": live_model, "voice": get_voice(), "resumed": _resumed_with, "attempt": <contador de tentativas consecutivas>, "backoff_s": getattr(self, "_conn_backoff", 3), "classified": <"quota"|"handle"|"net"|"key"|"config"|"other" calculado por função pura nova `errlog.classify_live_error(err_str)`>})`; acrescentar teste de `classify_live_error` em `tests/test_errlog_pure.py` (antes da implementação); zerar o contador ao conectar
- [x] T020a [US1] Correções achadas na 1ª rodada do T021 (2026-10-17), testes antes: (1) `classify_live_error` aceita a exceção (não só o texto) e desembrulha `BaseExceptionGroup`, `__cause__` e `__context__` — a queda real do Wi-Fi veio como `ExceptionGroup("unhandled errors in a TaskGroup")` com folhas `APIError 1006 abnormal closure`, `ConnectionClosedError` e `ConnectionResetError [WinError 64]` e foi classificada `other`; deve ser `net`; no gancho do `main.py` passar `e` em vez de `str(e)`; (2) em `record_failure`, para grupo de exceções, `error_type`/`message`/`origin` vêm da folha mais relevante (primeira que não é `CancelledError`), mantendo o traceback completo, para que causas diferentes não caiam na mesma assinatura `ExceptionGroup`; (3) em `core/gemini.py`, falha de rede no degrau (`ConnectError`, `getaddrinfo`, `ConnectionError`, `gaierror`) vira motivo `network` (hoje `error`) e `errlog.ladder_message` ganha o caso "todos os degraus sem rede": `SYS: Gemini unreachable — check the connection.`; (4) `classify_soft_failure` calibrado com os textos reais devolvidos pelas ações (`grep` dos `return f"…"` de erro em `actions/`), incluindo `Access denied: …` (devolvido pelo `file_controller` para arquivo inexistente), `Permission denied`, `… not found`, `No such file`, `Cannot …`/`Can't …`, sem pegar resultados legítimos ("No results" continua não sendo falha); (5) `errlog.asyncio_exception_handler(loop, context)` que registra `kind: "uncaught"`, `component: "app"` e chama o handler padrão, ligado no `main.py` com uma linha `asyncio.get_running_loop().set_exception_handler(errlog.asyncio_exception_handler)` junto do `errlog.bind(...)` — o `QueueFull` em callback do loop durante a reconexão só aparecia no console
- [x] T021 [US1] **[real]** *(feito em 2026-10-17, duas rodadas com o Irlan. Rodada 1 (`cad8641`): 5× crash de plugin = 1 grupo de 5, import quebrado registrado, queda real do Wi-Fi registrada (mas `classified: other` → corrigido no T020a), escada sem rede registrada, chave real e chave falsa plantada = 0 nos logs; falha mole `Access denied` perdida → T020a. Rodada 2 (`6743a32`): `soft:access denied` 3× num grupo, mais duas falhas reais do `browser_control` pegas (`Unknown browser action`, `Type error … Timeout`), chaves = 0. A queda da rede não se repetiu na rodada 2 (a conexão sobreviveu); a classificação `net` está coberta por teste com a cadeia de exceções real da rodada 1. Não feito: plugin em loop por 10 min (SC-008), coberto só pelos testes do limite de rajada. Achados do upstream fora do escopo: `open_app` diz "Opened …" para arquivo inexistente; `QueueFull` do áudio na reconexão; ramo de handle rejeitado do `main.py` casa "unhandled errors in a TaskGroup")* Validar US1 pelo [quickstart](quickstart.md) §4 e §5 com o app real: plugin de teste que lança no `run()` e outro que falha no import (criar em `plugins/` e **apagar depois**), falha mole (abrir arquivo inexistente), queda da rede ~10 s com a voz aberta; conferir `logs/failures.jsonl` (origem, contexto, versão, assinatura; 5 repetições = 1 assinatura) e a busca da chave nos logs = 0; plugin em loop 10 min → arquivos ≤ 25 MB com `suppressed`. Anotar resultados para o PR

**Checkpoint**: US1 completa e utilizável sozinha (MVP) — a `lv-triage` já tem o que ler.

---

## Phase 4: User Story 2 — Uso de LLM e custo (Priority: P2)

**Goal**: cada chamada avulsa, turno de voz e amostra de voz gera um `UsageRecord` em
`logs/usage-YYYY-MM.jsonl` com tokens por modalidade e custo estimado no pago; `tools/usage_report.py`
resume por dia/modelo.

**Independent Test**: quickstart §1 (testes de US2) + §6 (sessão real de 10 min, resumo, conta à mão).

### Testes (escrever antes; ver falhar)

- [x] T022 [P] [US2] Escrever `tests/test_llm_usage.py`: `tokens_from_usage()` com `SimpleNamespace` imitando REST (`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `cached_content_token_count`, `prompt_tokens_details=[{modality: "TEXT"|"AUDIO"|"IMAGE", token_count}]`) e Live (`response_token_count`, `response_tokens_details`) → campos `in_text, in_audio, in_image, in_cached, out_text, out_audio, thoughts, total`, campo ausente = `None` (não 0), sem detalhes → tudo em `*_text`; `cost()` com tabela de exemplo: preço simples, preço por modalidade (Live áudio ≠ texto), `thoughts` cobrado como saída de texto, `in_cached`, prefixo `models/` normalizado, modelo ausente → `(None, True)`, tokens `None` → `(None, False)`; `load_prices()` ignora entrada inválida (preço negativo/tipo errado) sem levantar; `LiveUsageMeter`: várias `usage_metadata` antes de um `turn_complete` → um registro com a **última** (regra provisória da research R7), `turn_complete` sem uso → `None`; `summarize()` com registros de dois dias e dois modelos → por dia/provedor/modelo `calls`, `ok`, `quota_errors`, `quota_hours`, tokens somados, `voice_minutes` (de `live_session` `end`), `cost_usd_if_paid`, `cost_unknown_calls`, totais e `models_without_price`; linha com horário perto da meia-noite cai no dia local certo; cada falta de cota traz também o **dia de cota do provedor** (data no Pacífico, que zera à meia-noite de lá = 4–5h em Brasília) ao lado do horário local (spec, Edge Cases "Fuso")
- [x] T023 [P] [US2] Acrescentar a `tests/test_gemini_hooks.py`: degrau REST falso que responde com `usage_metadata` conhecido → um `UsageRecord` `kind: "oneshot"`, `provider: "gemini"`, `model`, `tier_requested`, `billing: "free"`, `result: "ok"`, `latency_ms`, `tokens`, `cost_usd_if_paid` calculado, `cost_usd_actual: 0.0`; degrau que falha por cota antes → também um registro `result: "quota"` sem tokens; degrau `LIVE` cujo `_Reply` traz `usage_metadata` → registro com o modelo Live
- [x] T024 [P] [US2] Escrever `tests/test_usage_report.py`: chamar `tools/usage_report.py` por `main(argv)` com `--logs <tmp>` e registros sintéticos: `--days 14` imprime a tabela com cabeçalho de período e data dos preços; `--json` produz JSON que bate com `summarize()`; período sem registros → "nenhum registro no período" e código 0; argumento inválido → código 2; linha corrompida é ignorada e contada no rodapé; 14 dias × 600 registros/dia em < 5 s

### Implementação

- [x] T025 [P] [US2] Criar `config/llm_prices.json` no formato do [data-model](data-model.md#pricetable--configllm_pricesjson) (`v`, `checked: "2026-10-16"`, `source`, `unit`, `models`) com os valores de `docs/llm-gestao.md` §2 (Live 3.1/3.8 entrada 0,75 texto / 3,00 áudio, saída 4,50 / 12,00; 2.5 Flash-Lite 0,10/0,40; 2.5 Flash 0,30/2,50; Embedding 2 0,20; DeepSeek Flash 0,15 / cache 0,003 / 0,60 com `peak_multiplier` 2,0; DeepSeek V4 Pro 0,66/1,98; OpenAI `gpt-5-nano` 0,05/0,40, `gpt-5-mini` 0,25/2,00, `text-embedding-3-small` 0,02); conferir na página oficial de preços do Gemini (https://ai.google.dev/gemini-api/docs/pricing) os demais modelos da escada de `core/gemini.py` (`_LADDERS`, `LIVE_MODEL` e reserva `gemini-2.5-flash-native-audio`, TTS de `core/voice_preview.py`) e incluir os encontrados, citando a data; os não encontrados ficam fora (viram `price_missing`). Conferir que o arquivo **não** está no `.gitignore`
- [x] T026 [US2] Acrescentar `get_llm_billing() -> str` em `memory/config_manager.py` junto dos outros `get_*`, lendo a chave `llm_billing` de `config/api_keys.json` pelo mesmo caminho que os vizinhos usam e devolvendo `"free"` quando ausente ou inválida (`"free"|"paid"`)
- [x] T027 [US2] Implementar `core/llm_usage.py` (puras): `tokens_from_usage`, `cost`, `load_prices` (cache por mtime), `LiveUsageMeter`, `summarize` conforme [contracts/modules.md](contracts/modules.md) e data-model; T022 deve passar
- [x] T028 [US2] Implementar em `core/llm_usage.py` os registros: `record_oneshot`, `record_tts`, `live_session_start(model, voice, resumed)`, `live_message(response)` (lê só `response.usage_metadata` e `response.server_content.turn_complete`; alimenta o `LiveUsageMeter`; emite `kind: "live_turn"`), `live_session_end(reason)` (`duration_s`, `turns`, `end_reason` ∈ `voluntary|error|shutdown`), todos via `errlog._enqueue("usage", …)` no arquivo do mês, com `billing = get_llm_billing()`, `cost_usd_if_paid`, `cost_usd_actual` (0.0 se `free`), `price_missing`; modo diagnóstico: com `JARVIS_USAGE_RAW=1`, `live_message` também grava a `usage_metadata` crua em `logs/usage-raw.jsonl` (research R7)
- [x] T029 [US2] Ganchos de uso em `core/gemini.py`: em `call()`, no retorno de sucesso REST → `llm_usage.record_oneshot(model, tier, "ok", latency_ms, getattr(resp, "usage_metadata", None))`; no sucesso do degrau `LIVE` → idem com `_live_model()` e o `usage_metadata` do `_Reply`; em cada falha → `record_oneshot(model, tier, <motivo>, latency_ms)`; em `_live_turn.drain()` guardar a última `resp.usage_metadata` vista e devolvê-la junto do texto para o `_Reply` (atributo `usage_metadata`, sem mudar o contrato de `.text`); T023 deve passar
- [x] T030 [P] [US2] Gancho em `core/voice_preview.py`: medir latência em volta do `cl.models.generate_content(...)` e chamar `llm_usage.record_tts(model, "ok"|<motivo>, latency_ms, getattr(r, "usage_metadata", None))` no sucesso e na falha
- [x] T031 [US2] Ganchos de voz em `main.py`: `from core import llm_usage`; em `_receive_audio`, primeira linha dentro do loop de mensagens → `llm_usage.live_message(response)`; em `run()`, depois de `print("[JARVIS] Connected.")` → `llm_usage.live_session_start(live_model, get_voice(), _resumed_with)`; no `finally:` do loop de conexão → `llm_usage.live_session_end(<"voluntary" se foi sinal de reconexão, "error" se houve exceção, senão "shutdown">)` (guardar o motivo numa variável local já existente no fluxo ou numa nova de uma linha)
- [ ] T032 [US2] **[real]** Medir a semântica do `usage_metadata` do Live (research R7): rodar o app com `JARVIS_USAGE_RAW=1`, fazer 2–3 turnos de voz, ler `logs/usage-raw.jsonl` e decidir se chega uma vez ou várias por turno e se `prompt_token_count` é do turno ou acumulado; se for acumulado, mudar o `LiveUsageMeter` para emitir a diferença e ajustar o teste em `tests/test_llm_usage.py` (teste antes); registrar o achado com números em `specs/001-error-log-llm-usage/research.md` (R7) e num comentário do porquê em `core/llm_usage.py`
- [x] T033 [US2] Implementar `tools/usage_report.py` conforme [contracts/usage-report-cli.md](contracts/usage-report-cli.md) (`main(argv)`, `--days` 14 padrão, `--from/--to`, `--by day|model|provider`, `--json`, `--logs`), usando `llm_usage.summarize`; textos do relatório em pt-BR (é ferramenta do Irlan, não UI do app); T024 deve passar
- [ ] T034 [US2] **[real]** Validar US2 pelo [quickstart](quickstart.md) §6: sessão real de 10 min de voz com 3–4 chamadas avulsas (screenshot, pesquisa); `tools/usage_report.py --days 1` → ≥ 95% dos usos com tokens; conferir à mão o custo de 2 linhas (diferença < 1%). Anotar resultados para o PR

**Checkpoint**: US1 + US2 funcionando; dados para D10/D12 começam a acumular.

---

## Phase 5: User Story 3 — "Não funcionou" (Priority: P3)

**Goal**: `report_problem` grava em `logs/reports.jsonl` a expectativa do Irlan, as últimas ~3
trocas de fala, as últimas ferramentas e as falhas recentes; o JARVIS confirma curto.

**Independent Test**: quickstart §1 (teste de US3) + §7 (roteiro de 5 casos com voz).

### Testes (escrever antes; ver falhar)

- [ ] T035 [P] [US3] Escrever `tests/test_report_problem.py`: com `errlog.setup(log_dir, tee_console=False)`, `errlog.bind(transcript=lambda: [8 linhas falsas])`, 10 chamadas a `errlog.note_tool(...)` e 2 falhas recentes + 1 de 20 min atrás (relógio injetado/monkeypatch), chamar o handler de `actions/report_problem.py` com `{"expected": "abrir o Excel", "happened": "abriu o Word"}` → um registro em `reports.jsonl` com `kind: "report"`, `expected`, `happened`, `transcript` com no máximo 6 linhas (as últimas), `recent_tools` com as últimas 8, `recent_failures` só dos últimos 10 min (`{ts, signature, component, name, error_type}`), segredo plantado na transcrição mascarado; `expected` vazio → `"(not said)"`; o retorno ao modelo é `"Logged. Confirm in one short sentence."`; falhas automáticas em `failures.jsonl` **não** têm `transcript`; `discover_actions()` encontra `report_problem` com `TOOL` válido

### Implementação

- [ ] T036 [US3] Implementar `errlog.record_report(expected, happened=None)` em `core/errlog.py` conforme [data-model](data-model.md#problemreport--logsreportsjsonl): pega a transcrição pelo provedor ligado em `bind(transcript=…)` (até 6 linhas, cada uma cortada), as últimas 8 ferramentas do anel do `note_tool` e as falhas dos últimos 10 min (manter um anel das últimas falhas gravadas com `ts`, `signature`, `component`, `name`, `error_type`); grava em `reports.jsonl`
- [ ] T037 [US3] Criar `actions/report_problem.py` com o `TOOL` exato de [contracts/report_problem-tool.md](contracts/report_problem-tool.md) e o handler `report_problem(parameters)` que chama `errlog.record_report(...)` e devolve `"Logged. Confirm in one short sentence."` (ou `"Could not log it: <motivo>"`); docstring do módulo com a classe de risco (leitura) e o porquê de ser ação e não plugin (research R13); T035 deve passar
- [ ] T038 [US3] Medir o tamanho da declaração (texto de nome + descrições + nomes de parâmetros, e o JSON serializado) e atualizar a tabela "Custo de tokens" em `specs/001-error-log-llm-usage/plan.md` se mudou de 179/353; se o texto passar de 300, encurtar a descrição
- [ ] T039 [US3] **[real]** Roteiro de 5 casos de [contracts/report_problem-tool.md](contracts/report_problem-tool.md) com voz real: caso 1 registra em ≥ 4 de 5 tentativas com confirmação em voz; caso 2 pergunta antes; casos 4 e 5 não registram. Anotar resultados para o PR

**Checkpoint**: as três histórias funcionando.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T040 Medir o custo do registro na thread de quem chama (quickstart §3): script temporário no scratchpad (não versionado) com 10 000 `errlog.record_failure` com exceção real e 10 000 `llm_usage.record_oneshot`; p95 < 1 ms; e `tools/hud_bench.py` com registro em paralelo (p95 < 15 ms/quadro, `GL_RENDERER` da NVIDIA). Registrar os números no `plan.md` e no PR
- [ ] T041 [P] Atualizar `.claude/skills/lv-triage/SKILL.md` com os formatos reais (FR-022): arquivos de `logs/`, campo `v`, `kind`, `signature` (regra da research R5, sem número de linha), `kind: "soft"` é heurístico, relatos em `reports.jsonl` vêm primeiro, uso de LLM em `usage-*.jsonl` e `tools/usage_report.py --json`
- [ ] T042 [P] Atualizar `CLAUDE.md`: mapa do código (`core/errlog.py`, `core/redact.py`, `core/llm_usage.py`, `config/llm_prices.json`, `tools/usage_report.py`, `tests/`), seção de verificação (a base de `pytest` agora existe; `requirements-dev.txt`) e armadilha "o console agora também vai para `logs/jarvis.log`"; uma linha dizendo que tudo o que o JARVIS registra (inclusive a transcrição dos relatos) fica em `logs/`, legível e apagável à mão (Princípio VIII)
- [ ] T043 [P] Atualizar `CHANGELOG.md` (`## [Unreleased]` → `### Added`: registro de falhas, medição de uso de LLM, `report_problem`, base de testes) e `docs/demandas.md` §14 / `docs/VISAO.md` §6 com o estado (medição de duas semanas começa no merge)
- [ ] T044 Rodar a skill `lv-validate` completa: `py_compile` em todos os arquivos tocados, `pytest tests -q` (< 10 s), conferir `git status` sem nada de `logs/` nem segredo, `git diff irlan-dev -- main.py core/action_loader.py core/plugin_loader.py core/gemini.py core/voice_preview.py memory/config_manager.py` só com os ganchos previstos; `graphify update .`
- [ ] T045 Revisão independente com o agente `lv-reviewer` (diff contra constituição, spec e armadilhas) antes do PR; corrigir o que ele apontar

---

## Dependencies & Execution Order

### Phases

- **Setup (T001–T002)** → **Foundational (T003–T010)** → histórias.
- **US1 (T011–T021)** depende só da Foundational. É o MVP.
- **US2 (T022–T034)** depende da Foundational; usa `errlog._enqueue` e os mesmos pontos de
  `core/gemini.py` que US1 — fazer **depois** de US1 para não editar `core/gemini.py` e `main.py`
  em paralelo.
- **US3 (T035–T039)** depende da Foundational e de `note_tool`/anel de falhas de US1 (T014–T015).
  Pode correr em paralelo com US2 (arquivos diferentes: `actions/report_problem.py`, parte de
  `core/errlog.py` que US2 não toca — coordenar se dois agentes editarem `core/errlog.py`).
- **Polish (T040–T045)** depois das histórias desejadas.

### Dentro de cada história

Testes (falhando) → funções puras → registros → ganchos no upstream → validação **[real]**.

### Arquivos com mais de uma tarefa (não paralelizar entre si)

- `core/errlog.py`: T007, T008, T009, T010 (bind), T014, T015, T020 (`classify_live_error`), T036
- `core/gemini.py`: T018 → T029
- `main.py`: T010 → T019 → T020 → T031
- `tests/test_errlog_pure.py`: T004 → T011 → T020; `tests/test_gemini_hooks.py`: T013 → T023

## Parallel Examples

```text
# Foundational — testes juntos:
T003 tests/test_redact.py   |   T004 tests/test_errlog_pure.py

# US1 — testes juntos, depois loaders juntos:
T011 test_errlog_pure  |  T012 test_loader_hooks  |  T013 test_gemini_hooks
T016 core/action_loader.py  |  T017 core/plugin_loader.py

# US2 — testes e arquivos independentes juntos:
T022 test_llm_usage  |  T023 test_gemini_hooks (uso)  |  T024 test_usage_report  |  T025 config/llm_prices.json
T030 core/voice_preview.py  (paralelo a T029/T031)

# Polish:
T041 lv-triage  |  T042 CLAUDE.md  |  T043 CHANGELOG/docs
```

## Implementation Strategy

1. **MVP = Setup + Foundational + US1** (T001–T021): falhas registradas, agrupadas, sem segredos,
   escada esgotada visível. Já resolve "erros somem ao fechar" e alimenta a `lv-triage`.
2. **US2** (T022–T034): começa a contagem das duas semanas de uso para D10/D12 — quanto antes
   mesclar, antes a decisão de custo tem dado.
3. **US3** (T035–T039): relato do usuário.
4. **Polish** (T040–T045) e PR único para `irlan-dev` com a verificação registrada (Princípio II).
   Se preferir entregar antes, MVP pode ir num PR próprio e US2/US3 num segundo.
