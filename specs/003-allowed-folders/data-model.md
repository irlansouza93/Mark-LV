# Data model: Pastas liberadas (spec 003)

## Pasta liberada (`AllowedFolder`)

Guardada em `config/api_keys.json`, chave `allowed_folders` (lista), lida/gravada só por
`memory/config_manager.py`.

```json
"allowed_folders": [
  {"path": "C:\\Users\\irlan", "mode": "rw", "label": "Home"},
  {"path": "C:\\DESENVOLVIMENTO", "mode": "ro"}
]
```

| Campo | Tipo | Regra |
|---|---|---|
| `path` | str | absoluto; gravado já normalizado (`realpath`); duplicatas (mesmo `normcase`) viram uma só, ficando o último modo |
| `mode` | `"ro"` \| `"rw"` | qualquer outro valor → `"ro"` (o lado seguro) e registro da falha |
| `label` | str, opcional | só para a interface |

- **Chave ausente** (primeira execução depois da atualização): o padrão é
  `[{"path": <pasta do usuário>, "mode": "rw"}]` (clarificação 2026-10-22). O padrão **não** é
  gravado até o usuário salvar em ⚙ SETUP, para que uma mudança futura do padrão alcance quem nunca
  mexeu.
- **Chave corrompida** (não é lista, item sem `path`): itens inválidos são descartados com
  `errlog.record_failure`; se nada sobra, vale o padrão acima. Nunca "tudo liberado".
- **Pasta que não existe mais**: continua na lista (o disco pode estar desconectado); a interface
  a marca como ausente. A política a trata normalmente (nada lá dentro existe para ler).
- **Pasta que é ou está dentro de item negado** (ex.: `~/.ssh`): `save_allowed_folders` recusa e
  devolve o motivo; a interface mostra.

## Item negado (`DenyRule`)

Fixo no código (`core/fs_policy.py`), montado uma vez por sistema operacional. Não é salvo.

| Campo | Tipo | Exemplo |
|---|---|---|
| `kind` | `"path"` \| `"parts"` \| `"name"` | — |
| `value` | caminho normalizado / tupla de componentes / padrão de nome | `~/.ssh` · `("config","api_keys.json")` · `*.kdbx` |
| `reason` | str curta, mostrada ao usuário | `"SSH keys"`, `"app secrets"`, `"password vault"`, `"git internals"` |

Conteúdo completo em [`research.md` R3](research.md#r3--lista-de-negação-forma-e-conteúdo-fr-007).

## Operação (`Op`)

`"read"` · `"write"` · `"exec"` — mapa das ações em [`contracts/fs_policy.md`](contracts/fs_policy.md).

## Decisão (`Decision`)

Valor imutável devolvido por `fs_policy.check(...)`.

| Campo | Tipo | Significado |
|---|---|---|
| `allowed` | bool | — |
| `path` | `Path` | caminho normalizado conferido |
| `op` | `Op` | — |
| `reason` | `"ok"` \| `"uploaded"` \| `"outside"` \| `"read_only"` \| `"denied"` \| `"invalid"` | por que |
| `root` | `Path \| None` | pasta liberada mais específica que contém o caminho (se houver) |
| `deny_reason` | str \| None | o `reason` da regra negada, se `reason == "denied"` |
| `suggest` | `Path \| None` | pasta a liberar: a raiz do disco/pasta do usuário mais próxima do alvo que **não** contém item negado; para `read_only`, a própria `root` |
| `actor` | str | ação que pediu (`"file_controller"`, …) — vai para o registro |

`str(decision)` (só quando recusada) é a frase `[FOLDER_POLICY] …` do contrato.

## Registro da recusa

Uma linha em `logs/failures.jsonl` (via `errlog.record_failure`), passando pelo filtro de segredos:

```json
{"kind": "policy", "component": "fs_policy", "name": "file_controller",
 "error_type": "policy:read_only", "message": "write C:\\DESENVOLVIMENTO\\x.txt",
 "context": {"op": "write", "root": "C:\\DESENVOLVIMENTO", "reason": "read_only"}}
```

Itens negados registram só `deny_reason`, nunca conteúdo.

## Transições

Não há estado além da lista: a política relê a lista do `config_manager` quando ela muda
(`fs_policy.reload()`, chamado pelo `save_allowed_folders`) — FR-018, sem reiniciar.
