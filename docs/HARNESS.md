# Ambiente de desenvolvimento com Claude Code (harness)

Como o Claude Code trabalha neste repositório: o que ele lê, o que roda sozinho, que modelo faz
cada coisa e como uma ideia vira um PR. Vale para qualquer sessão, local ou na nuvem.

## Camadas

| Camada | Onde | Papel |
|---|---|---|
| Mapa e convenções | `CLAUDE.md` | Carregado em toda sessão: mapa do código, convenções, armadilhas, fluxo |
| Regras | `.specify/memory/constitution.md` | O "como" não negociável; conferido em todo plano e PR |
| Visão e decisões | `docs/VISAO.md`, `docs/demandas.md` | O "o quê e por quê"; o roteiro |
| Memória do projeto | `docs/VISAO.md` §4, `docs/conversas/` | Decisões com data e o resumo das conversas que as geraram |
| Specs | `specs/NNN-nome/` | `spec.md` → `plan.md` → `tasks.md` de cada feature (Spec Kit) |
| Navegação | `graphify-out/` (local, fora do git) | `graphify query/path/explain` antes de ler arquivos às cegas |
| Verificação automática | `.claude/hooks/` | `py_compile` em todo `.py` editado; guarda do graphify |
| Procedimentos | `.claude/skills/` | `speckit-*` (Spec Kit), `lv-validate`, `lv-new-skill`, `lv-triage` |
| Agentes por modelo | `.claude/agents/` | `lv-implementer`, `lv-quick`, `lv-reviewer` |

## Fluxo de uma feature

1. **Ideia → spec.** Na sessão principal, a partir de `irlan-dev` atualizada:
   `/speckit-specify <descrição>` cria a branch `NNN-nome` e `specs/NNN-nome/spec.md`.
2. **Esclarecer.** `/speckit-clarify` faz as perguntas que mudam o desenho.
3. **Planejar.** `/speckit-plan` (com o Constitution Check) e `/speckit-tasks`.
4. **Conferir.** `/speckit-analyze` cruza spec, plano e tarefas antes de escrever código.
5. **Implementar.** `/speckit-implement` na sessão principal **ou** tarefa por tarefa com os
   agentes abaixo.
6. **Verificar.** Skill `lv-validate`; revisão com `lv-reviewer` (ou `/code-review`).
7. **Fechar.** `CHANGELOG.md`, `graphify update .`, PR para `irlan-dev`.

Correção pequena sem spec: branch `fix/…` a partir de `irlan-dev`, mesmo passo 6 e 7.

## Qual modelo faz o quê

Os agentes do projeto escolhem o modelo no próprio arquivo (`model:` no cabeçalho), então a troca
acontece sozinha quando a sessão principal delega. A sessão principal usa o modelo escolhido no
seletor do app.

| Trabalho | Quem | Modelo | Por quê |
|---|---|---|---|
| Especificar, esclarecer, planejar, analisar | Sessão principal | O mais forte disponível (Opus) | Erro aqui custa a feature inteira |
| Tarefa já descrita em `tasks.md` (código + verificação) | `lv-implementer` | Sonnet | O desenho já foi decidido; executar bem e barato |
| Mudança mecânica (renomear, texto, comentário, doc, config) | `lv-quick` | Haiku | Rápido e barato; recusa o que exige decisão |
| Revisão do diff contra a constituição | `lv-reviewer` | Opus | Revisor diferente de quem implementou |
| Busca ampla no código | `Explore` (embutido) ou graphify | — | Só a conclusão volta para a sessão |

Regras: um agente barato que encontra uma decisão de desenho **para e devolve** à sessão principal;
nenhum agente faz commit, push ou PR — isso é da sessão principal, com o usuário.

## Hooks (o que roda sozinho)

- **PostToolUse `Edit|Write`** → `.claude/hooks/py-compile.sh`: compila o `.py` recém-editado com o
  Python do `.venv`; erro de sintaxe volta na hora para o Claude corrigir.
- **PreToolUse** → `graphify hook-guard`: lembra de consultar o grafo antes de buscas e leituras
  amplas.
- **SessionStart** → `.claude/hooks/session-start.sh`: em sessão na nuvem, instala o PowerShell 7
  (os scripts do Spec Kit são `.ps1`). Na máquina local não faz nada.

## Economia de tokens (decidido em 2026-10-19)

O limite do plano acaba por conversa longa, não por código difícil: cada mensagem relê todo o
histórico da sessão. Regras para todo trabalho neste repositório:

1. **Uma sessão por spec ou tarefa.** Fechou o ciclo (spec mesclada, fix mesclado), encerre a sessão.
   A próxima começa do zero com um **prompt curto** que aponta para os docs; nunca recole histórico.
   A memória do projeto está em `docs/VISAO.md`, `docs/conversas/` e `specs/`, não no chat.
2. **Conversa ficou longa?** Use `/compact` ou abra uma sessão nova com um resumo de 5 linhas.
3. **Sem cadeia de sessões.** Abra a sessão da feature direto e converse com ela. Uma sessão "mãe"
   que recebe cada relato das filhas paga a releitura do próprio histórico a cada relato. Na spec
   001, as rodadas de teste geraram dezenas de avisos na sessão de planejamento.
4. **Navegar antes de ler.** `graphify query`/`explain`/`path` para entender o código; `grep`
   pontual para achar uma linha; `Read` com `offset`/`limit`, nunca o `ui.py` (~6 mil linhas)
   inteiro.
5. **Modelo certo para cada trabalho** (tabela "Qual modelo faz o quê" acima): o modelo forte só
   especifica, planeja e revisa; as tarefas do `tasks.md` vão para o `lv-implementer` (Sonnet), as
   mecânicas para o `lv-quick` (Haiku) e as buscas amplas para o `Explore`, de onde só a conclusão
   volta. Tarefa mecânica roda com esforço baixo.
6. **Saída curta de comandos:** `pytest -q`, `| tail`, `--stat`; nunca despejar log inteiro.
7. **Teste real em lote:** um único roteiro com todos os casos num turno só, em vez de várias rodadas
   de ida e volta.
8. **`CLAUDE.md` enxuto:** ele entra em toda sessão. Detalhe vai para `docs/`, com um link no
   `CLAUDE.md`.

## Versão

`55.MINOR.PATCH` (D6 da `VISAO.md`). Cada PR de feature acrescenta a entrada em
`CHANGELOG.md` → "Unreleased"; ao fechar uma versão, a seção ganha número e data e a tag `v55.x.y`
é criada em `irlan-dev`.
