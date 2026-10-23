# Contrato: `core/fs_policy.py`

Módulo puro (sem Qt, sem rede). Toda ação/plugin que toca um caminho escolhido pelo modelo usa
estas funções; nenhuma tem regra própria (FR-002, FR-010).

## API

```python
Op = Literal["read", "write", "exec"]

class PolicyDenied(Exception):
    decision: Decision          # str(exc) == a frase [FOLDER_POLICY] para o modelo

def check(path: str | Path, op: Op, *, actor: str) -> Decision
    """Decide sem efeito colateral além do registro da recusa. Nunca levanta exceção:
    caminho inválido → Decision(allowed=False, reason="invalid")."""

def require(path: str | Path, op: Op, *, actor: str) -> Path
    """check(); permitido → devolve o caminho normalizado (a ação usa ESTE, não o bruto);
    recusado → PolicyDenied."""

def is_denied(path: str | Path) -> str | None
    """Motivo da lista de negação, ou None. Usado por varreduras item a item (sem realpath)."""

def walk(root: Path, *, actor: str) -> Iterator[tuple[Path, list[str], list[str]]]
    """os.walk(followlinks=False) sobre uma raiz já permitida para leitura, podando pastas negadas
    e omitindo arquivos negados (FR-009, R7)."""

def confined(base: Path, rel: str) -> Path
    """base / rel normalizado; levanta PolicyDenied se o resultado sair de base (FR-012):
    caminho absoluto, '..', unidade diferente, link que aponta para fora."""

def allowed_folders() -> list[AllowedFolder]
def deny_rules() -> list[DenyRule]                 # só leitura, para ⚙ SETUP
def validate_folder(path: str | Path) -> str | None # motivo para não aceitar a pasta, ou None
def reload() -> None                               # relê a lista do config_manager
def bind_uploaded(fn: Callable[[], str | None]) -> None   # R5

# Só para testes (FR-020): troca as fontes, sem tocar config/ nem o disco real.
def configure(*, folders=None, home=None, app_dir=None, env=None, platform=None, uploaded=None) -> None
```

## Ordem da decisão

1. Normalizar (R2). Falhou → `invalid`.
2. Lista de negação → `denied` (vence tudo, inclusive o arquivo enviado e o desfazer).
3. `op == "read"` e o caminho é o arquivo enviado atual → `uploaded` (permitido).
4. Pasta liberada mais específica que contém o caminho; nenhuma → `outside`.
5. `op in ("write", "exec")` e modo `ro` → `read_only`.
6. → `ok`.

`exec` só decide a pasta; a confirmação da interface é responsabilidade da ação (FR-006,
research R6).

## Frase de recusa (para o modelo)

```
[FOLDER_POLICY] I did not <read|change|run> "<path>": <motivo>. Tell the user in their own
language, in one short sentence, <o que fazer>. Do not try another path to get around it.
```

| `reason` | `<motivo>` | `<o que fazer>` |
|---|---|---|
| `outside` | it is outside the folders they allowed | that they can allow `<suggest>` in ⚙ SETUP → ALLOWED FOLDERS |
| `read_only` | `<root>` is allowed as read-only | that they can switch it to read and write in ⚙ SETUP → ALLOWED FOLDERS |
| `denied` | it is a protected location (`<deny_reason>`) that can never be allowed | that this location is always off limits |
| `invalid` | the path is not valid | to say the path again |

## Mapa ação → operação

| Ação / ponto | Operação | Observação |
|---|---|---|
| `file_controller` list, read, find, largest, info | `read` | busca e listagem via `walk` |
| `file_controller` disk_usage | — (fora) | só espaço livre do disco, nunca conteúdo (FR-013) |
| `file_controller` create_file, create_folder, write, rename, delete, organize_desktop | `write` | delete: o conjunto de pastas protegidas continua (R9) |
| `file_controller` move | `write` na origem e no destino | — |
| `file_controller` copy | `read` na origem, `write` no destino | — |
| `code_helper` explain, edit (leitura), screen_debug com `file_path` | `read` | — |
| `code_helper` write, edit (gravação), optimize com `output_path` | `write` | destino padrão = Área de Trabalho |
| `code_helper` run, build | `exec` + confirmação (`report=True`) | build: escreve, depois confirma o ciclo inteiro |
| `file_processor` ler o arquivo | `read` (arquivo enviado passa pelo passo 3) | — |
| `file_processor` gravar resultado / extrair em `destination` | `write`; cada item do arquivo compactado via `confined` | — |
| `desktop` wallpaper `path` | `read` | — |
| `desktop` organize, clean | `write` na Área de Trabalho | — |
| `desktop` tarefa livre (exec de código do modelo) | confirmação (`report=True`) | não há caminho a decidir; o banner mostra o código |
| `video_player` `source` local | `read` | URL não passa pela política |
| `browser_control`, `computer_control` screenshot `path` | `write` | sem `path` → padrão de hoje, também conferido |
| `dev_agent` arquivos gerados | `confined(projeto, nome)` + `write` em `PROJECTS_DIR` | instalar + rodar: uma confirmação (`report=True`) |

## Mudança em `core/confirm.py`

```python
def bind(show, hide, log=None, deliver=None) -> None        # deliver(text) novo, opcional
def request(key, title, detail, run, *, report: bool = False) -> str
```

`report=True`: ao terminar `run()` (ou ao cancelar/expirar), chama
`deliver("[CONFIRMED_RESULT] <title>: <resultado até 4000 caracteres>")`
(ou `"[CONFIRMED_RESULT] <title>: the user cancelled it."`; sem resposta em `TIMEOUT_SECONDS`, um temporizador entrega `"…: the confirmation expired, nothing was run."`; substituída por outro `request`, `"…: replaced by a newer request, nothing was run."`). Sem `deliver`, só o log de hoje.
Chamadores existentes (`computer_settings`) não mudam.

## Mudança em `core/errlog.py`

`_kind_for`: `component == "fs_policy"` → `"policy"`. Nada mais.
