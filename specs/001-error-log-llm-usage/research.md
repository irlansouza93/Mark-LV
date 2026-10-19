# Research — 001 Registro de falhas e medição de uso de LLM

Levantamento feito em 2026-10-16 sobre `irlan-dev` (`8f9b865`), Python 3.14.3 do `.venv`,
`google-genai` 2.29.0. Cada item: **Decisão**, **Por quê**, **Alternativas rejeitadas**.

## R1. Onde as falhas nascem hoje (e onde dá para pegá-las sem editar cada ação)

Medido no código:
- `core/action_loader.py` — `ActionRegistry.run()` captura exceção do handler, imprime o traceback e
  devolve `"Tool '<nome>' failed: <erro>"`; `discover_actions()` captura erro de import/validação.
- `core/plugin_loader.py` — o mesmo em `PluginRegistry.run()` e `discover_plugins()`.
- `main.py` — `_execute_tool()` tem um `except` geral para as ferramentas inline; o loop de conexão
  (`run()`, `except BaseException`) classifica cota/handle/rede/chave e reconecta.
- `core/gemini.py` — `call()` anda na escada, classifica cada falha (`is_quota_error`,
  `is_gone_error`, `is_unavailable_error`) e só faz `print`. Devolve `None` quando todos falham.
- **As ações engolem quase todos os erros sozinhas**: 260+ blocos `except Exception` em `actions/`,
  que devolvem textos como `"Could not save: …"`, `"Click error: …"`, `"… failed: …"`. Essas
  falhas nunca chegam ao loader.

**Decisão:** três camadas, nenhuma exige editar arquivo de `actions/`:
1. **Exceção real** nos pontos que já capturam (loaders, `_execute_tool`, loop de conexão,
   escada): uma linha `errlog.record_failure(...)` com o objeto da exceção (traceback completo).
2. **Falha "mole"** (soft failure): um gancho único em `_execute_tool` (`errlog.note_tool(nome,
   args, resultado)`) vê o texto devolvido por **toda** ferramenta (inline, ação, plugin). Um
   classificador conservador marca como falha os resultados que começam com padrões de erro
   conhecidos (`Could not`, `Unable to`, `… failed`, `… error:`, `Error`) — registrados com
   `kind: "soft"`, sem traceback, para a triagem saber que é heurística. Os textos de crash do
   loader (`Tool '…' failed`, `Sir, the '…' plugin failed`) são excluídos para não contar duas vezes.
3. **Não capturado**: `sys.excepthook` e `threading.excepthook` registram o que escapar de uma
   thread (instalados no `setup()`, sem tocar no upstream).

O mesmo `note_tool` alimenta o anel das "últimas ferramentas" que o `report_problem` usa.

**Alternativas rejeitadas:** editar os 260 `except` das ações (viola o Princípio I e quebra o
merge com o upstream); interceptar só `traceback.print_exc` (perde as falhas que só devolvem texto).

## R2. Gravar sem atrasar a thread Qt nem o áudio

**Decisão:** quem chama só monta um `dict` pequeno (com o objeto da exceção, não o traceback
formatado) e o põe numa fila em memória (`queue.SimpleQueue`). **Uma thread daemon de escrita**
faz o resto: formata o traceback, calcula a assinatura, aplica o filtro de segredos, corta textos,
serializa em JSON e grava. O log de texto usa `logging` padrão com `QueueHandler` →
`QueueListener` (mesma ideia, biblioteca padrão). Na saída do processo, `atexit` esvazia a fila
com prazo de 1 s. Meta medida na implementação: < 1 ms por registro na thread de quem chama
(SC-006); a formatação de traceback (≈0,1–1 ms) fica fora dela por isso.

Se a fila passar de 10 mil itens (disco travado), novos registros são descartados e um contador
de descartados vai no próximo registro gravado — memória nunca cresce sem limite.

**Alternativas rejeitadas:** gravar direto na thread de quem chama (I/O de disco no loop de
áudio viola o Princípio III); `asyncio` (metade dos chamadores são threads de executor, não o loop).

## R3. O console também vai para o arquivo

**Decisão:** `setup()` troca `sys.stdout`/`sys.stderr` por um "tee" que escreve no stream original
e põe a linha na fila do log de texto (`logs/jarvis.log`). Assim os ~centenas de `print()` e
`traceback.print_exc()` do upstream entram no arquivo **sem editar nenhum deles** (FR-001).
O tee nunca levanta exceção (erro de encoding do console do Windows continua como hoje).
Rotação: `RotatingFileHandler`, 2 MB × 5 arquivos.

**Alternativa rejeitada:** trocar `print` por `logging` no código todo (diff enorme no upstream).

## R4. Filtro de segredos

**Decisão:** função pura `redact(value, secrets)` que percorre `str`/`dict`/`list` e mascara:
- **valores literais** das chaves configuradas: todos os valores-string de `config/api_keys.json`
  com 16+ caracteres (chave Gemini e credenciais de plugin), relidos quando o arquivo muda (mtime);
- **formatos conhecidos**: Google `AIza[0-9A-Za-z_-]{35}`, OpenAI/DeepSeek `sk-[A-Za-z0-9_-]{20,}`
  (inclui `sk-proj-`), Anthropic `sk-ant-…`, token de bot do Telegram `\d{8,10}:[A-Za-z0-9_-]{35}`,
  `Bearer <token>`, `Authorization: …`, parâmetros de URL `key=`/`token=`/`access_token=`;
- **campos por nome** (em dicts): chave que contém `key`, `token`, `secret`, `password`,
  `authorization`, `cookie` → valor inteiro mascarado.
Máscara: `‹redacted:AIza…f3›` (prefixo de 4 e sufixo de 2 para reconhecer *qual* chave vazou, sem
revelá-la). Aplicado no thread de escrita a **todo** registro (falhas, relatos, uso, log de texto).
Testado com segredos plantados em mensagem, contexto, args e traceback (SC-003).

## R5. Assinatura e agrupamento

**Decisão:** `signature = sha1(f"{component}:{name}|{error_type}|{origin}")[:12]`, onde
- `error_type` = nome da classe da exceção; para eventos sem exceção, o motivo classificado
  (`quota`, `unavailable`, `gone`, `timeout`, `ladder_exhausted`, `soft:<prefixo>`);
- `origin` = `arquivo/relativo.py:função` do **frame mais interno dentro do projeto** (ignora
  `.venv`, `site-packages`, stdlib). Sem número de linha (muda a cada edição — Assumption da spec);
  a linha fica no registro (`origin_line`).
- `soft:<prefixo>` = as 3 primeiras palavras do resultado, minúsculas, com números, aspas e
  caminhos removidos (ex.: `soft:could not save`).
`group(records)` (pura) devolve grupos com contagem, primeira/última vez, versões e um exemplo,
ordenados por contagem. A `lv-triage` usa a mesma regra.

## R6. Versão

**Decisão:** lida uma vez no `setup()`, sem chamar `git`: lê `.git` (pasta, ou arquivo
`gitdir:` no caso de worktree) → `HEAD` → ref solta ou `packed-refs` → commit curto. Mais a última
versão `## [55.x.y]` do `CHANGELOG.md`. Formato: `55.1.0+8f9b865`; sem git: `55.1.0+nogit`; sem
nada: `unknown`. Função pura sobre o texto dos arquivos, testável.

## R7. Uso de LLM: o que o SDK devolve

Medido no `google-genai` 2.29.0:
- REST (`generate_content`): `response.usage_metadata` = `GenerateContentResponseUsageMetadata`
  com `prompt_token_count`, `candidates_token_count`, `thoughts_token_count`,
  `cached_content_token_count`, `tool_use_prompt_token_count`, `total_token_count`,
  `prompt_tokens_details`/`candidates_tokens_details` (lista de `ModalityTokenCount{modality,
  token_count}`).
- Live (`LiveServerMessage.usage_metadata`) = `UsageMetadata` com `prompt_token_count`,
  `response_token_count`, `thoughts_token_count`, `cached_content_token_count`,
  `prompt_tokens_details`, `response_tokens_details` (por modalidade: TEXT/AUDIO/IMAGE…).

**Incerteza a medir (Princípio IX):** no Live, se `usage_metadata` chega uma vez por turno ou
várias, e se `prompt_token_count` é do turno ou acumulado na sessão (a doc não garante; a
`llm-gestao.md` supõe que o contexto é "recontado a cada turno").
**Decisão:** a primeira tarefa de voz da implementação grava, numa sessão real curta com a chave,
as mensagens `usage_metadata` cruas (modo diagnóstico `JARVIS_USAGE_RAW=1`) para fixar a regra.
Até lá o medidor (`LiveUsageMeter`) guarda a **última** `usage_metadata` vista antes de cada
`turn_complete` e emite um registro por turno; se a medição mostrar contagem acumulada, o medidor
passa a emitir a diferença — a regra fica num ponto só e coberta por teste com mensagens falsas.

**Medido (T032, 2026-10-17, `gemini-3.1-flash-live-preview`, 7 turnos de voz, `JARVIS_USAGE_RAW=1`):**
- Chega **1 `usage_metadata` por turno**, na **mesma mensagem** do `turn_complete`.
- Os valores são **do turno, não acumulados**: `prompt_token_count` foi 20152 → 21510 → 22038 →
  11348 → 11404 → 11919 → 24399 (cai e sobe, porque é o contexto daquele turno); a resposta foi
  414, 134, 17, —, 323, 166, 118.
- O prompt de cada turno tem ~16,4–17,1 mil tokens de **texto** (prompt do sistema + declarações das
  ferramentas, recontados a cada turno — a suposição da `llm-gestao.md` está certa), mais **áudio**
  (586–3030) e, às vezes, **imagem** (264/528).
- Custo no pago: US$ 0,009–0,024 por turno.
- **Regra confirmada:** "a última `usage_metadata` antes do `turn_complete`, um registro por turno".
  Somar os registros por turno dá o que seria cobrado; o medidor **não** precisa subtrair o
  snapshot anterior.
- Consequência para a decisão de custo (D10/D12): o grosso do custo de uma conversa curta é o
  contexto fixo reenviado a cada turno (~17 mil tokens de texto), não a fala do usuário.

Chamadas avulsas pelo degrau `LIVE` (`_live_turn`, sessão descartável): o `drain()` passa a
guardar a `usage_metadata` das mensagens e o `_Reply` a carrega; o registro sai como
`kind: "oneshot"`, modelo = o modelo Live usado.

Amostras de voz (`core/voice_preview.py`, TTS): registradas como `kind: "tts"` com o
`usage_metadata` da resposta.

Pesquisa (`SEARCH`) já passa por `gemini.call()` — coberta pelo mesmo gancho.

## R8. Tabela de preços

**Decisão:** `config/llm_prices.json` (versionado; não é segredo), com `checked` (data), `source`
(URLs) e entradas por `provider` + `model`, preço em US$ por 1M tokens, separado por modalidade de
entrada/saída quando difere (Live: áudio ≠ texto), mais `cached_input` quando houver.
Valores iniciais de `docs/llm-gestao.md` §2: Live 3.1/3.8 (entrada 0,75 texto / 3,00 áudio; saída
4,50 / 12,00), 2.5 Flash-Lite (0,10 / 0,40), 2.5 Flash (0,30 / 2,50), Embedding 2 (0,20),
DeepSeek Flash/Pro, OpenAI nano/mini/embedding-3-small. Os outros degraus da escada (3.5/3.1
Flash-Lite, 3.5/3.6 Flash, `*-latest`, 2.5 native audio, TTS) **não têm preço no documento**: uma
tarefa da implementação confere na página oficial de preços do Gemini e completa a tabela; o que
não for achado fica de fora e gera custo `null` + `price_missing: true` (FR-015), e o resumo lista
esses modelos para o Irlan completar.
`thoughts_token_count` é cobrado como saída (regra do Gemini). Custo gravado no registro na hora
(FR-013: mudar a tabela não reescreve o passado).

**Alternativa rejeitada:** preços no código (FR-013 pede edição sem mexer em código).

## R9. Nível grátis/pago

**Decisão:** campo `tier` em todo registro de uso; nesta spec sempre `"free"`, lido de uma flag
`llm_billing` em `config/api_keys.json` via `config_manager` (ausente = `free`). O custo gravado é
sempre `cost_usd_if_paid`; `cost_usd_actual` = 0 quando `free`. A spec 004/modo privado liga o
`paid` por chave.

## R10. Retenção e arquivos

**Decisão (spec, clarify):**
| Arquivo | Conteúdo | Limite |
|---|---|---|
| `logs/jarvis.log` (+ `.1`…`.5`) | console (tee) + avisos do `logging` | 2 MB × 5 = 12 MB |
| `logs/failures.jsonl` (+ `.1`…`.4`) | falhas (`exception`, `soft`, `gemini`, `live`, `uncaught`) | 5 MB × 4 = 25 MB |
| `logs/reports.jsonl` (+ `.1`, `.2`) | relatos `report_problem` | 1 MB × 3 |
| `logs/usage-YYYY-MM.jsonl` | uso de LLM, um arquivo por mês | apaga meses > 12 |
A limpeza de meses antigos roda no `setup()` (função pura que recebe a lista de nomes e o mês
atual → quais apagar). **Rajada**: a mesma assinatura gravada mais de 20 vezes em 60 s passa a
virar um único registro `"suppressed": N` a cada minuto — conta tudo, não enche o disco (SC-008).

## R11. Horário em que a cota volta

O nível grátis zera à meia-noite do Pacífico. **Medido:** o Python do `.venv` no Windows **não**
tem o banco de fusos (`ZoneInfo('America/Los_Angeles')` falha; `tzdata` não instalado).
**Decisão:** usar `zoneinfo` quando disponível (Linux/macOS) e, se não, uma função pura com a regra
de horário de verão dos EUA (2º domingo de março 02:00 → 1º domingo de novembro 02:00; UTC−7/−8).
Testada nas viradas. Converte para o fuso local do sistema (`datetime.astimezone()`).
**Alternativa rejeitada:** adicionar `tzdata` (dependência nova só para uma linha de aviso).

## R12. Aviso de escada esgotada (FR-004a)

**Decisão:** `core/gemini.py` chama `errlog.record_failure(component="gemini",
name="ladder", error_type="ladder_exhausted", …)` e `errlog.notify_once("ladder", msg,
every_s=300)`. `errlog` tem um notificador ligado pelo `main.py` (`errlog.bind(notify=
self.ui.write_log)`, ao lado do `confirm_gate.bind`) — `JarvisUI.write_log` já é thread-safe.
Texto (inglês, como o resto do log): `SYS: Gemini free quota used up on 8 models — back at 04:00.`
ou `SYS: Gemini models not answering (8 tried) — try again shortly.` Intervalo de 300 s = o
`_COOLDOWN_SECONDS` da escada (antes disso nada mudou).

## R13. `report_problem`

**Decisão:** arquivo novo `actions/report_problem.py` com `TOOL` (auto-descoberto, sempre ligado;
arquivo novo não conflita com merge do upstream). Classe de risco: **leitura** (só escreve o
próprio log do app). Bloqueante e instantâneo (< 5 ms: monta o dict e põe na fila). O handler
pede ao `errlog` o contexto: últimas 3 trocas de fala (via provedor ligado pelo `main.py`:
`errlog.bind(transcript=lambda: list(self._session_log[-6:]))`), últimas 8 ferramentas do anel do
`note_tool` e falhas dos últimos 10 min. Devolve ao modelo uma frase curta para confirmar.
Declaração-alvo ≤ 300 caracteres (descrição + parâmetros `expected` obrigatório, `happened`
opcional); tamanho medido vai para o plano (FR-018).
**Alternativa rejeitada:** plugin (pode ser desligado; um relato é infraestrutura) e ferramenta
inline em `main.py` (mais linhas no upstream).

## R14. Base de testes

**Decisão:** `pytest` em `requirements-dev.txt` (não entra no `requirements.txt` do app),
`tests/` com `conftest.py` que põe a raiz no `sys.path` e fornece `tmp_path` como pasta de logs.
Módulos com lógica pura separada do I/O: `core/redact.py`, funções puras de `core/errlog.py`
(assinatura, agrupamento, soft-failure, versão, supressão de rajada, retenção) e de
`core/llm_usage.py` (custo, medidor do Live, resumo, horário da cota). Testes nunca importam
`main.py`, `ui.py`, `google.genai` ou rede; objetos do SDK são simulados com `SimpleNamespace`.
