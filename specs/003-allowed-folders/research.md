# Research: Pastas liberadas (spec 003)

Levantamento do código feito em 2026-10-22 na `irlan-dev` (`f7b7dcb`). Cada item: decisão, porquê,
alternativas.

## R1 — Onde a lista de pastas fica salva

- **Decisão:** chave `allowed_folders` em `config/api_keys.json`, lida e gravada só por
  `memory/config_manager.py` (`get_allowed_folders()` / `save_allowed_folders()` sobre o
  `_patch_config` que já existe). Formato em [`data-model.md`](data-model.md).
- **Porquê:** é onde todas as preferências do app já moram (voz, estilo de HUD, espera…), e o
  `CLAUDE.md` define o `config_manager` como a única porta desse arquivo. O arquivo já está no
  `.gitignore` e na lista de negação, então o modelo não consegue editá-lo por uma ação.
- **Alternativas:** arquivo próprio `config/allowed_folders.json` (como `holo_looks.json`) — mais
  um segredo a proteger e a ignorar no git, sem ganho.

## R2 — Normalização do caminho (FR-004)

- **Decisão:** `os.path.realpath(p, strict=False)` sobre o caminho já expandido (`~`, variáveis de
  ambiente não), seguido de `os.path.normcase` para comparar. Para um caminho que ainda não existe,
  o `realpath` resolve o pai existente mais próximo e anexa o resto — e o resto não pode conter
  `..` (já foi eliminado pelo `realpath`). Contêiner: `commonpath([raiz, alvo]) == raiz` em forma
  `normcase`, nunca `startswith` (que confunde `C:\DEV` com `C:\DEVELOPMENT`).
- **Porquê:** no Windows o `realpath` (3.8+) segue links simbólicos **e junções** e devolve o nome
  longo via `GetFinalPathNameByHandle` (resolve `PROGRA~1`); o `normcase` dá a comparação sem
  maiúsculas no Windows e é identidade no Linux. Mesma função nos três sistemas (Princípio IX).
- **Alternativas:** `Path.resolve()` — equivalente no 3.14, mas o `file_controller` já mostrou
  que a exceção engolida (`except: return False`) esconde a causa; o módulo central trata o erro e
  diz o motivo. Comparação por `Path.is_relative_to` sem `normcase` falha com maiúsculas no Windows.
- **macOS:** APFS costuma ser insensível a maiúsculas, mas `normcase` lá é identidade. Aceito: o
  pior caso é uma recusa a mais (`/Users/x/desktop` × `Desktop`), nunca uma liberação a mais; a
  lista de negação compara nomes com `casefold()` em todos os sistemas, então ela não tem esse furo.

## R3 — Lista de negação: forma e conteúdo (FR-007)

- **Decisão:** três tipos de regra, todas comparadas com `casefold()` sobre o caminho já
  normalizado:
  1. **Caminho exato/pasta absoluta** calculada na carga do módulo: segredos do app relativos à
     pasta do app (`config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`,
     `memory/long_term.json`) e locais por sistema (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.azure`,
     `~/.kube`, `~/.docker/config.json`, `~/.git-credentials`, `~/.netrc`, `~/.pypirc`,
     `~/.npmrc`; Windows: `%APPDATA%\Microsoft\Credentials`, `…\Protect`, perfis de
     Chrome/Edge/Brave/Firefox, `%APPDATA%\Bitwarden`, `%LOCALAPPDATA%\1Password`, `KeePass`;
     macOS: `~/Library/Keychains`, perfis em `~/Library/Application Support/<navegador>`; Linux:
     `~/.local/share/keyrings`, `~/.password-store`, `~/.mozilla`, `~/.config/google-chrome`,
     `~/.config/chromium`, `~/.config/BraveSoftware`).
  2. **Sequência de componentes em qualquer nível**: `config/api_keys.json`, `config/certs`,
     `config/whatsapp_web` — porque os worktrees (`.claude/worktrees/*/config/api_keys.json`,
     copiados à mão segundo o `CLAUDE.md`) e outros clones do Mark LV têm a mesma chave fora da
     pasta do app. E qualquer componente `.git` (o interior de um repositório).
  3. **Extensão/nome de arquivo**: `*.kdbx`, `*.kdb`, `*.1pux`, `*.opvault`, `*.pem`, `*.key`,
     `*.p12`, `*.pfx`, `id_rsa*`, `id_ed25519*`, `id_ecdsa*`, e `.env` / `.env.*` exceto
     `.env.example`/`.env.sample`/`.env.template`.
- **Porquê:** a regra 2 fecha o vazamento mais provável do próprio projeto (`C:\DESENVOLVIMENTO`
  liberada contém o Mark LV, o Mark LVI e os worktrees); a regra 3 cobre chaves copiadas para
  qualquer pasta. `.env` entra porque é onde projetos guardam chaves — a auditoria da spec 011 não
  precisa ler o valor das chaves.
- **Alternativas:** lista editável pelo usuário — recusada pelo FR-008 (o modelo poderia ser
  levado a pedir que se tire algo dela). Negar `.gitignore`/`.github` junto com `.git` — não:
  o FR-007 só nega o interior de `.git/`.

## R4 — Recusa: texto para o modelo e registro (FR-015, FR-016)

- **Decisão:** a recusa é uma frase que começa com o marcador `[FOLDER_POLICY]`, seguida do
  motivo e do que fazer, em inglês (como todo retorno de ferramenta), instruindo o modelo a
  explicar na língua do usuário — mesmo padrão do `[CONFIRMATION_PENDING]` de `core/confirm.py`.
  O registro vai para `logs/failures.jsonl` via `errlog.record_failure(component="fs_policy", …)`
  com um tipo novo `kind: "policy"` (uma linha em `errlog._kind_for`).
- **Porquê:** o marcador `[` não casa com nenhum padrão de `_SOFT_START`/`_SOFT_LABELLED`
  (`core/errlog.py:324`), então o `note_tool` **não** conta a recusa de novo como falha mole; o
  `kind` próprio deixa o `lv-triage` separar "o JARVIS recusou por política" (comportamento certo)
  de "algo quebrou". A spec dizia "falha mole"; o efeito pedido — não disparar relato e não ser
  erro do app — é atendido melhor pelo `kind: "policy"` (spec ajustada no FR-016).
- **Alternativas:** não registrar — perde-se o sinal de "o Irlan tentou e a pasta não estava
  liberada" (que mostra o que liberar); registrar como `soft:` — misturaria com erros engolidos.

## R5 — Arquivo enviado pela interface (FR-011)

- **Decisão:** `fs_policy.bind_uploaded(fn)` recebe um callable que devolve o arquivo enviado
  atual (`lambda: self.ui.current_file`, ligado no `main.py` junto dos outros `bind`). Uma leitura
  de exatamente esse arquivo (mesmo caminho normalizado) é permitida fora das pastas liberadas;
  a lista de negação é conferida antes.
- **Porquê:** o `main.py:1661` já usa `ui.current_file` para preencher o `file_path` do
  `file_processor`; perguntar na hora (callable) evita copiar estado e acompanha a troca/remoção
  do arquivo na interface sem código novo no `ui.py`.
- **Alternativas:** liberar a pasta do arquivo enviado — amplo demais.

## R6 — Confirmação para executar código do modelo (FR-006)

- **Fato:** `core/confirm.request(key, title, detail, run)` devolve na hora uma frase para o
  modelo e, se o usuário confirmar, roda `run()` numa thread; **o resultado só vai para o log**
  (`core/confirm.py:143`). Para desligar o PC isso basta; para "roda o script", o modelo precisa
  ouvir a saída.
- **Decisão:** `confirm.request` ganha um parâmetro opcional `report: bool = False`. Com `True`,
  o resultado (ou a recusa/expiração) é entregue ao modelo como mensagem
  `[CONFIRMED_RESULT] <título>: <saída cortada>` por um callback `deliver` registrado no
  `confirm.bind(...)` existente (`main.py:2673`). No `main.py`, o `deliver` usa o mesmo caminho do
  texto digitado (`session.send_client_content(..., turn_complete=True)`), que já é o canal do
  arquivo arrastado e do quiz (`ui.py:5694`). Sem sessão ativa, fica só no log.
- **O que pede confirmação** (o banner mostra o arquivo e o comando):
  - `code_helper` `run`: o arquivo e o interpretador.
  - `code_helper` `build`: o código é escrito primeiro (escrita reversível, em pasta liberada); a
    confirmação cobre o ciclo inteiro "rodar → corrigir → rodar" (até as tentativas de hoje), para
    não pedir uma confirmação por tentativa.
  - `dev_agent`: um único pedido antes de instalar dependências e rodar o projeto (instalar
    também é irreversível pelo Princípio IV); gerar os arquivos continua sem confirmação.
  - `desktop` tarefa livre (`exec` de código do modelo, `actions/desktop.py:97`): o banner mostra
    as primeiras linhas do código.
- **Porquê:** cumpre o Princípio IV sem bloquear a sessão (a confirmação é assíncrona, Princípio
  III) e sem uma segunda ida e volta de ferramenta.
- **Alternativas:** bloquear a ferramenta esperando o clique — prende uma thread por até 90 s e o
  modelo fica em silêncio (contra o Princípio III).

## R7 — Desempenho em varreduras (SC-005)

- **Decisão:** a raiz de uma listagem/busca é conferida uma vez com `realpath`. Os itens de dentro
  são conferidos só contra a lista de negação por **comparação de texto** dos componentes
  relativos (sem `realpath` por item) e a varredura não desce em pastas negadas nem segue links
  de pasta (`os.walk(followlinks=False)` / `rglob` sem seguir links, padrão do 3.13+). Um item que
  seja link só aparece pelo nome; abri-lo passa pela conferência completa.
- **Porquê:** um `realpath` no Windows custa uma abertura de handle por item; fazer isso em 10 mil
  arquivos dobraria a busca. Comparação de texto é microssegundos. A medida (10 mil arquivos,
  antes × depois) vai no `quickstart.md` e no PR.
- **Alternativas:** cache de `realpath` por pasta — mais código, só necessário se a medida falhar.

## R8 — Testes com links e junções (FR-020, SC-003)

- **Decisão:** testes em `tmp_path` com a política configurada por injeção (lista de pastas, pasta
  do usuário e pasta do app passadas ao módulo, sem ler `config/`). Link simbólico via
  `os.symlink` com `pytest.skip` quando o Windows não dá o privilégio; **junção** via
  `_winapi.CreateJunction` (não exige administrador) só no Windows. Forma curta: teste no Windows
  só se `GetShortPathNameW` devolver um nome diferente (volumes podem ter 8.3 desligado).
- **Porquê:** os casos de escape que o SC-003 exige precisam existir de verdade no disco; sem
  privilégio de link, a junção cobre o mesmo furo no Windows.

## R9 — Proteções que já existem no `file_controller`

- **Decisão:** ficam onde estão: a lixeira, o desfazer e o conjunto de pastas que não podem ser
  apagadas (`actions/file_controller.py:279`). `_SAFE_ROOTS`/`_is_safe_path` passam a delegar
  para `fs_policy` (a função continua existindo, com uma linha, para o diff no arquivo do upstream
  ser pequeno — Princípio I).
- **Desfazer (clarificação 2026-10-22):** as funções de desfazer registradas no `push_undo` já
  capturam os caminhos e não passam pela política; nada a mudar além de não envolvê-las.

## R10 — Custo na declaração das ferramentas (FR-021, Princípio VI)

- **Decisão:** só duas descrições mudam: `file_controller.path` ganha "(allowed folders only)"
  (+22 caracteres) e `code_helper.file_path` o mesmo (+22). Total ≈ +44 caracteres em toda
  conexão; nenhuma ferramenta nova. As recusas explicam o resto quando acontecem.
