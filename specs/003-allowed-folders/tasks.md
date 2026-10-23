# Tasks: Pastas liberadas (política central de arquivos)

**Input**: Design documents from `specs/003-allowed-folders/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/fs_policy.md](contracts/fs_policy.md), [quickstart.md](quickstart.md)

**Tests**: obrigatórios pela constituição (Princípio II: lógica sem interface tem `pytest` escrito
antes e visto falhando). Testes nunca leem `config/` real, rede ou interface: usam `tmp_path` e
`fs_policy.configure(...)`.

**Organization**: por história de usuário. US1–US3 são P1; US4 é P2.

**Regras para quem implementa (lv-implementer / lv-quick):**
- Toda tarefa que tem teste: escreva o teste, **veja falhar**, depois implemente (Princípio II).
- Comentários explicam o porquê com números medidos, no tom do código vizinho; código e textos de
  UI em inglês.
- Arquivos do upstream (`main.py`, `ui.py`, `actions/*`) são guardados com **CRLF**: depois de
  editar, `git diff --stat` deve mostrar só as linhas novas; se virou reescrita, restaure o CRLF.
- Diff pequeno e localizado nos `actions/*` (Princípio I): nada de reformatar.
- A ação usa o caminho **devolvido** por `fs_policy.require(...)`, não o bruto.
- Recusa: `except fs_policy.PolicyDenied as e: return str(e)` — nunca `except: pass`.
- Rodar: `.venv/Scripts/python.exe -m pytest tests -q`. Nenhum agente faz commit/push/PR.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [x] T001 Criar o esqueleto `core/fs_policy.py` com a docstring do módulo (o problema de hoje: `file_controller` só aceita `Path.home()`, `code_helper` lê qualquer caminho — ver spec §Contexto), os tipos `Op`, `AllowedFolder`, `DenyRule`, `Decision` (dataclasses congeladas, campos exatos de [data-model.md](data-model.md)) e `PolicyDenied(Exception)` com `.decision`; funções do [contrato](contracts/fs_policy.md#api) com `raise NotImplementedError` — para os testes importarem

---

## Phase 2: Foundational (bloqueia todas as histórias)

**⚠️** Nenhuma ação muda antes desta fase estar verde.

### Testes primeiro (devem falhar antes de T006–T009)

- [x] T002 [P] Escrever `tests/test_fs_policy.py` — decisão básica: com `configure(folders=[(tmp/"ro", "ro"), (tmp/"rw", "rw")], home=tmp/"home", app_dir=tmp/"app")`: ler em `ro` → `ok`; escrever em `ro` → `read_only` com `root`; escrever em `rw` → `ok`; `exec` em `ro` → `read_only`; fora de tudo → `outside` com `suggest`; pastas aninhadas (`ro` contendo `ro/sub` em `rw`) → vale a mais específica (FR-005); `C:\DEV` liberada não cobre `C:\DEVELOPMENT` (use `tmp/"dev"` × `tmp/"development"`); caminho de arquivo que ainda não existe dentro de `rw` → `ok`; `tmp/"rw"/".."/"x"` → `outside`; caminho inválido (`"\0"`) → `invalid`, sem exceção; no Windows, pasta liberada `\\server\share` cobre `\\server\share\a.txt` e não `\\server\other` (só por string, `skipif` fora do Windows); `require` devolve o `Path` normalizado e levanta `PolicyDenied` cujo `str()` começa com `[FOLDER_POLICY]` e contém o texto de "o que fazer" da tabela do contrato
- [x] T003 [P] Escrever em `tests/test_fs_policy.py` os testes da lista de negação (US2, SC-003): com a raiz que contém cada item liberada em `rw`, recusar ler/escrever/exec em: `app/config/api_keys.json`, `app/config/certs/x.pem`, `app/config/whatsapp_web/a`, `app/memory/long_term.json`, `home/.ssh/id_ed25519`, `home/.aws/credentials`, um `cofre.kdbx` em qualquer pasta, `proj/.git/config`, `proj/.env` (mas `proj/.env.example` e `proj/.gitignore` permitidos), e **`outro_clone/config/api_keys.json` fora de `app_dir`** (regra de componentes, research R3); maiúsculas trocadas (`CONFIG/API_KEYS.JSON`) também recusadas; `is_denied` devolve o motivo; `validate_folder(home/".ssh")` devolve motivo e `validate_folder(home)` devolve `None`; com `configure(platform="darwin")` e `configure(platform="linux")` as regras incluem `~/Library/Keychains` e `~/.local/share/keyrings` respectivamente (FR-022)
- [x] T004 [P] Escrever em `tests/test_fs_policy.py` os testes de escape (FR-004, SC-003): link simbólico dentro de `rw` apontando para pasta fora → decisão do destino (`outside`), e apontando para `home/.ssh` → `denied` (`os.symlink`; `pytest.skip` se `OSError`/sem privilégio); no Windows, **junção** via `_winapi.CreateJunction` cobrindo os mesmos dois casos (`skipif sys.platform != "win32"`); forma curta 8.3 só se `ctypes.windll.kernel32.GetShortPathNameW` devolver nome diferente, senão `skip`; `confined(base, "../x.py")`, `confined(base, "C:/x.py" ou "/x.py")`, `confined(base, "a/../../x")` → `PolicyDenied`; `confined(base, "utils/h.py")` → `base/utils/h.py`
- [x] T005 [P] Escrever em `tests/test_fs_policy.py` os testes de estado: arquivo enviado (`bind_uploaded(lambda: str(f))`) fora das pastas → `read` = `uploaded`, `write` = `outside`; arquivo enviado dentro de item negado → `denied`; troca do callable para `None` → `outside`; `walk(raiz)` não devolve `.git/`, `*.kdbx`, `.ssh/` e não segue link de pasta; `reload()` depois de mudar a fonte de pastas muda a decisão seguinte (FR-018); **toda recusa** chama `errlog.record_failure(component="fs_policy", name=<actor>, error_type="policy:<reason>", ...)` (monkeypatch no `errlog`), e permissões não registram nada

### Implementação

- [x] T006 Implementar normalização e decisão em `core/fs_policy.py`: `_norm(p)` = `os.path.realpath(os.path.expanduser(str(p)), strict=False)` + `os.path.normcase` para comparar (research R2; erro → `invalid`); contêiner por `os.path.commonpath` em forma `normcase`, nunca `startswith`; `check`/`require` na ordem exata da seção "Ordem da decisão" do contrato; `suggest` conforme data-model ("a raiz do disco/pasta do usuário mais próxima do alvo que **não** contém item negado; para `read_only`, a própria `root`" — na prática: para `outside`, a pasta existente mais próxima do alvo que seja filha direta da raiz do disco ou da pasta do usuário); frase de recusa exatamente no modelo do contrato (`[FOLDER_POLICY] I did not <read|change|run> "<path>": …`); `configure(...)` para testes
- [x] T007 Implementar a lista de negação em `core/fs_policy.py` (research R3, os três tipos `path`/`parts`/`name`, comparação `casefold()` em todos os sistemas), montada uma vez por sistema a partir de `home`, `app_dir` e variáveis `APPDATA`/`LOCALAPPDATA` (injetáveis via `configure(env=..., platform=...)`, `platform` padrão `sys.platform`); `is_denied`, `deny_rules`, `validate_folder`; `walk(root, actor=)` com `os.walk(followlinks=False)` podando pastas negadas e omitindo arquivos negados por comparação de texto dos componentes (sem `realpath` por item — research R7); `confined(base, rel)`
- [x] T008 Implementar `bind_uploaded`, `reload` e o registro da recusa em `core/fs_policy.py`: `errlog.record_failure(component="fs_policy", name=actor, error_type=f"policy:{reason}", message=f"{op} {path}", context={"op":…, "root":…, "reason":…})` importado dentro de `try/except` (o módulo segue funcionando sem `errlog`); itens negados registram só `deny_reason`, nunca conteúdo
- [x] T009 [P] Em `core/errlog.py` `_kind_for`: `component == "fs_policy"` → `"policy"` (uma linha + comentário do porquê, research R4); acrescentar o caso em `tests/test_errlog_failures.py` (ou no teste de `_kind_for` que já exista)
- [x] T010 Em `memory/config_manager.py`: `get_allowed_folders() -> list[dict]` e `save_allowed_folders(items) -> str | None` sobre `_patch_config`, regras de [data-model.md](data-model.md#pasta-liberada-allowedfolder) ao pé da letra: `mode` só `"ro"`|`"rw"` ("qualquer outro valor → `"ro"`" e registro da falha); chave ausente → padrão `[{"path": <pasta do usuário>, "mode": "rw"}]` **sem gravar**; itens inválidos descartados com `record_failure`; nada sobra → padrão; duplicatas por `normcase` → uma, último modo; `save` recusa pasta que `fs_policy.validate_folder` rejeita (devolve o motivo) e chama `fs_policy.reload()` depois de gravar; `allowed_folders_error() -> str | None` diz se a última leitura caiu no padrão por configuração corrompida (para o painel, T025). `fs_policy` lê a lista por `config_manager.get_allowed_folders()` (import tardio, para os testes poderem trocar a fonte por `configure`). Testes em `tests/test_fs_policy.py` com `CONFIG_FILE` apontado para `tmp_path` (monkeypatch)

**Checkpoint**: `pytest tests -q` verde; T002–T005 vistos falhando antes de T006.

---

## Phase 3: User Story 1 — Liberar uma pasta e usá-la por voz (P1) 🎯 MVP

**Goal**: `file_controller` obedece à lista: lê em `ro`, grava só em `rw`, recusa fora com a frase do contrato.
**Independent Test**: `tests/test_fs_policy_actions.py::test_file_controller_*` com disco em `tmp_path`.

- [x] T011 [US1] Escrever `tests/test_fs_policy_actions.py` para `file_controller` (chamando `actions.file_controller.file_controller({...})` com `fs_policy.configure`): list/read em `ro` funcionam; create_file/write/rename/delete/move em `ro` → texto começando com `[FOLDER_POLICY]` e **nada mudou no disco**; copy de `ro` para `rw` funciona, de `rw` para `ro` recusa; move exige escrita na origem e no destino; find numa raiz com `.git/` e `.env` não lista esses itens; o desfazer (`core.undo.undo_last()`) de um create feito em `rw` funciona depois de a pasta virar `ro` (clarificação 2026-10-22)
- [x] T012 [US1] Em `actions/file_controller.py`: `_SAFE_ROOTS`/`_is_safe_path` passam a delegar para `fs_policy` (manter a função, uma linha, research R9) e cada ponto que hoje chama `_is_safe_path` (linhas ~201–628) passa a chamar `fs_policy.require(target, <op>, actor="file_controller")` com a operação do [mapa do contrato](contracts/fs_policy.md#mapa-ação--operação), usando o caminho devolvido e devolvendo `str(e)` na recusa; `find`/`largest` usam `fs_policy.walk`; `disk_usage` fica **fora** da política (só lê o espaço livre do disco, nunca conteúdo; hoje não confere nada — exigir leitura em `C:\` seria regressão, SC-007); manter o conjunto de pastas protegidas do delete (linha ~279), a lixeira e o `push_undo` como estão (o desfazer não passa pela política)
- [x] T013 [US1] Criar `tools/fs_policy_bench.py`: cria 10 mil arquivos em pasta temporária (subpastas de 100), mede `file_controller` `find` 5 vezes com a política e 5 com `fs_policy.walk` trocado por `os.walk` puro, imprime as medianas e a diferença em %; rodar e registrar o número no comentário do porquê de `fs_policy.walk` (alvo SC-005: ≤ 10 %)

**Checkpoint**: US1 testável sozinha; o que funciona hoje na pasta do usuário continua (SC-007).

---

## Phase 4: User Story 2 — Segredos nunca alcançados (P1)

**Goal**: a lista de negação vale através das ações, não só no módulo.
**Independent Test**: `tests/test_fs_policy_actions.py::test_denied_*`.

- [x] T014 [US2] Acrescentar em `tests/test_fs_policy_actions.py`: com a pasta que contém `config/api_keys.json` liberada em `rw`, `file_controller` read/copy/move/list desse arquivo e de `.ssh/` recusam com o motivo "protected location" e sem ler o conteúdo (monkeypatch em `Path.read_text`/`open` para falhar se chamado); `file_controller` `list` da pasta do app não mostra `api_keys.json`
- [x] T015 [US2] Ajustar o que o T014 apontar em `actions/file_controller.py` (ex.: `list` e `info` usando o filtro de negação nos itens) — sem nova regra local

---

## Phase 5: User Story 3 — Todas as ações pela mesma regra (P1)

**Goal**: as demais ações da tabela do Contexto consultam `fs_policy`; execução de código do modelo passa pela confirmação com retorno do resultado.
**Independent Test**: um teste por ação em `tests/test_fs_policy_actions.py` (caminho fora → recusa, disco intacto) e `tests/test_confirm_report.py`.

### Confirmação com retorno (base das execuções)

- [x] T016 [US3] Escrever `tests/test_confirm_report.py`: `confirm.bind(show, hide, deliver=fake)`; `request(..., run=lambda: "out", report=True)` + `resolve(True)` → `fake` recebe `"[CONFIRMED_RESULT] <title>: out"` (esperar a thread com `join`/evento); `resolve(False)` → `"[CONFIRMED_RESULT] <title>: the user cancelled it."`; sem resposta em `TIMEOUT_SECONDS` (monkeypatch para 0.05) → `"…: the confirmation expired, nothing was run."`; um segundo `request` com o primeiro pendente → o primeiro recebe `"…: replaced by a newer request, nothing was run."`; resultado com mais de 4000 caracteres é cortado; sem `deliver` ou `report=False` → nada entregue (comportamento de hoje); `request` sem `show` ligado continua recusando
- [x] T017 [US3] Em `core/confirm.py`: `bind(show, hide, log=None, deliver=None)` e `request(key, title, detail, run, *, report: bool = False)` conforme [contrato](contracts/fs_policy.md#mudança-em-coreconfirmpy); o worker entrega o resultado/cancelamento quando `report`; com `report=True`, um `threading.Timer(TIMEOUT_SECONDS)` (daemon) entrega a expiração se a mesma confirmação ainda estiver pendente, e um `request` que substitui outro pendente com `report` entrega "replaced" a ele (FR-006); `computer_settings` não muda
- [x] T018 [US3] Em `main.py` (perto de `main.py:2673`, onde `confirm_gate.bind` é chamado): passar `deliver=` que envia o texto ao modelo pelo mesmo caminho de `_on_text_command` (`asyncio.run_coroutine_threadsafe(self.session.send_client_content(turns={"role":"user","parts":[{"text": text}]}, turn_complete=True), self._loop)`), sem sessão → só `write_log`; e `fs_policy.bind_uploaded(lambda: self.ui.current_file)` no mesmo bloco (research R5)

### Ações (independentes entre si depois de T017)

- [x] T019 [P] [US3] `actions/code_helper.py`: `_read_file` → `require(read)`; `_save_file`/`_resolve_save_path` → `require(write)` (destino padrão continua a Área de Trabalho); `_run_action` e o ciclo de execução de `_build` → `require(exec)` no arquivo e depois `confirm.request(key="code_run", title=f"Run {path.name}", detail=f"{interp} {path}", run=<o que roda hoje>, report=True)`; no `build`, escrever o código antes e confirmar o ciclo inteiro rodar→corrigir (research R6). Testes em `tests/test_fs_policy_actions.py` (explain fora → recusa sem ler; run em `rw` → devolve `[CONFIRMATION_PENDING]` e nada roda até `resolve(True)`) com `subprocess.run` trocado por fake
- [x] T020 [P] [US3] `actions/file_processor.py`: no início de `file_processor`, `require(file_path, "read", actor="file_processor")` (o arquivo enviado passa pelo passo 3 do contrato); toda gravação de resultado → `require(write)`; extração de arquivo compactado: `require(destination, "write")` e cada membro via `fs_policy.confined(destino, nome)` — membro que escapa é pulado e citado no retorno. Testes: arquivo fora e não enviado → recusa; enviado → lê; zip com `../evil.txt` não cria o arquivo fora
- [x] T021 [P] [US3] `actions/desktop.py`: `path` do wallpaper → `require(read)`; organize/clean → `require(desktop, "write")`; a tarefa livre que faz `exec` do código do modelo (`actions/desktop.py:97`) passa por `confirm.request(key="desktop_task", title="Run a desktop task", detail=<primeiras 12 linhas do código>, run=<exec de hoje>, report=True)`. Testes com o `exec` trocado por fake
- [x] T022 [P] [US3] `actions/video_player.py` (`source` local, não URL → `require(read)`), `actions/browser_control.py` e `actions/computer_control.py` (`path` da captura, e o padrão quando ausente → `require(write)`). Testes: caminho fora → recusa, nenhum arquivo criado
- [x] T023 [P] [US3] `actions/dev_agent.py`: `_write_file` usa `fs_policy.confined(project_dir, file_path)` e `require(write)` (arquivo que escapa → pulado e citado, o resto segue — FR-012); instalar dependências + rodar o projeto viram **uma** `confirm.request(key="dev_agent_run", title=f"Install and run {project}", detail=<deps + run_command>, run=<instalação + _run_project de hoje>, report=True)`; abrir o VS Code continua sem confirmação. Testes com `subprocess` e o Gemini trocados por fakes
- [x] T024 [US3] `plugins/_template.py`: exemplo comentado de `fs_policy.require(path, "read", actor=PLUGIN["name"])` e do tratamento de `PolicyDenied` (FR-014)

**Checkpoint**: SC-002 — cada ação da tabela do Contexto tem teste de recusa sem efeito no disco.

---

## Phase 6: User Story 4 — ⚙ SETUP → ALLOWED FOLDERS (P2)

**Goal**: ver/adicionar/mudar modo/remover pastas sem editar JSON; negação mostrada só para leitura.
**Independent Test**: abrir o painel, mudar a lista, conferir `config/api_keys.json` e uma decisão sem reiniciar.

- [x] T025 [US4] Em `ui.py`: classe `AllowedFoldersOverlay(_HudOverlay)` no padrão de `MemoryOverlay` (`ui.py:2523`): lista das pastas com modo (combo READ ONLY / READ & WRITE), marca "missing" quando a pasta não existe, botões ADD (`QFileDialog.getExistingDirectory`), REMOVE e SAVE; SAVE chama `config_manager.save_allowed_folders` e mostra o motivo se recusado (ex.: `~/.ssh`); ao adicionar uma pasta que **contém** item negado (ex.: a pasta do usuário), mostra o aviso "protected items inside stay off limits" sem recusar; quando `get_allowed_folders` caiu no padrão por configuração corrompida, mostra o aviso no topo do painel (precisa de `allowed_folders_error() -> str | None` em `config_manager`, feito no T010); seção recolhível "Always protected" com `fs_policy.deny_rules()` (só leitura). Botão `📁  ALLOWED FOLDERS` no ⚙ SETUP junto dos outros (perto de `🧠  MEMORY`, ~`ui.py:5076`) e `_open_allowed_folders` no padrão de `_open_memory_panel` (`ui.py:6387`). Textos em inglês. Nada lento na thread Qt
- [ ] T026 [US4] Verificar: `py_compile ui.py`; abrir o app, adicionar `C:\DESENVOLVIMENTO` em READ ONLY, salvar, conferir `allowed_folders` em `config/api_keys.json` e que `fs_policy.check` muda sem reiniciar (SC-006); tentar adicionar `~/.ssh` → motivo na tela

---

## Phase 7: Polish & Cross-Cutting

- [x] T027 [P] Descrições das ferramentas (R10): `file_controller` parâmetro `path` e `code_helper` parâmetro `file_path` ganham " (allowed folders only)"; registrar no PR o total de caracteres somados (≈ +44)
- [x] T028 [P] `CHANGELOG.md` (Unreleased: spec 003), `CLAUDE.md` (linha `core/fs_policy.py` no mapa do código; armadilha "todo caminho escolhido pelo modelo passa por `fs_policy.require`; `config/api_keys.json` guarda `allowed_folders`"), `docs/demandas.md` §2 (estado: implementado na spec 003), `docs/VISAO.md` §6 se surgir algo em aberto
- [x] T029 Rodar a skill `lv-validate`: `py_compile` em tudo que mudou, `pytest tests -q`, `tools/fs_policy_bench.py` (número no PR), `git diff --stat` conferindo CRLF nos arquivos do upstream
- [x] T030 `graphify update .`
- [x] T031 Revisão independente com `lv-reviewer` (diff × constituição × spec × armadilhas)
- [x] T032 Teste real: roteiro único da spec (passos 1–7 e 4b) com a chave, fora do sandbox; resultado de cada passo e as linhas `kind: "policy"` de `logs/failures.jsonl` vão para o PR

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T010)**: T002–T005 em paralelo (mesmo arquivo de teste, mas escritos de uma vez por um só agente); T006→T007→T008 em sequência (mesmo arquivo); T009 em paralelo; T010 depois de T007 (usa `validate_folder`).
- **US1 (T011–T013)** depois da Foundational. **US2 (T014–T015)** depois de T012.
- **US3**: T016→T017→T018; depois T019–T023 em paralelo (arquivos diferentes; cada um acrescenta seus testes em `tests/test_fs_policy_actions.py` — se dois agentes rodarem ao mesmo tempo, cada um usa um arquivo `tests/test_fs_policy_<ação>.py`); T024 a qualquer momento depois de T006.
- **US4 (T025–T026)** depende só da Foundational (T010).
- **Polish** por último; T031 antes de T032; T032 antes do PR.

## Parallel Example: User Story 3

```text
Depois de T017–T018:
  lv-implementer A: T019 (code_helper)
  lv-implementer B: T020 (file_processor)
  lv-implementer C: T021 + T022 (desktop, video_player, browser/computer_control)
  lv-implementer D: T023 (dev_agent)
```

## Implementation Strategy

- **MVP = Foundational + US1 + US2**: `C:\DESENVOLVIMENTO` alcançável em leitura, segredos
  protegidos, `file_controller` pela política. Já destrava a spec 011 (leitura de projetos).
- **Depois US3** (o furo do `code_helper` e a confirmação de execução) — necessária antes do merge:
  sem ela a constituição (Princípio IV) continua violada.
- **US4** por último; até lá a lista pode ser editada à mão no `config/api_keys.json`.
- Um único PR para `irlan-dev` com tudo, depois do teste real (T032).
- Modelos: T001–T026 → `lv-implementer` (Sonnet); T027–T028, T030 → `lv-quick` (Haiku);
  T031 → `lv-reviewer`; T029/T032 na sessão principal.
