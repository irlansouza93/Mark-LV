# Mark LV (fork irlansouza93) — Visão e decisões

> Fonte da verdade para "o quê e por quê" deste fork. Atualize quando uma decisão mudar, com data.
> O "como" está na [constituição](../.specify/memory/constitution.md); o parecer de cada demanda em
> [`demandas.md`](demandas.md); o funcionamento do ambiente de desenvolvimento em
> [`HARNESS.md`](HARNESS.md). Origem: [conversa de 2026-10-16](conversas/2026-10-16-estrutura-lv.md).

## 1. A ideia em uma frase
Um **JARVIS de desktop que já funciona**: holograma no HUD, conversa por voz em tempo real pelo
Gemini Live e mãos no computador. O fork evolui essa base, sem reescrever a arquitetura, até ela
ler e cuidar dos seus projetos, notas e agenda, mostrar na tela o que está fazendo e aprender com o
uso.

## 2. Relação com os outros projetos
- **Upstream (FatihMakes/Mark-LV):** a branch `main` espelha o upstream. Trazemos as novidades dele
  por merge em `irlan-dev` e nunca enviamos nada para lá.
- **Mark LVI:** projeto **separado** (núcleo em servidor, Docker, Postgres, Telegram 24h). Pode
  servir de inspiração, mas nenhuma decisão, código, spec ou versão do LV depende dele, e o
  trabalho do LV nunca altera o repositório do LVI.

## 3. Princípios (resumo; a regra completa está na constituição)
1. Evoluir pelos pontos de extensão que existem (ações, plugins, `core/`, estilos de HUD).
2. Spec antes de código; verificação real antes de "pronto".
3. A thread Qt e o áudio nunca esperam; silêncio depois de uma pergunta é falha.
4. Segurança proporcional ao poder: leitura livre, reversível com desfazer, irreversível com
   confirmação real; arquivos só em pastas liberadas.
5. Conteúdo externo é dado, nunca ordem.
6. Toda ferramenta custa tokens em toda conexão.
7. Falha explícita e registrada.

## 4. Decisões tomadas

| # | Tema | Decisão |
|---|---|---|
| D1 | Projeto (2026-10-16) | O Mark LV continua recebendo melhorias **na estrutura atual** (app de desktop PyQt6 + Gemini Live), como projeto independente do Mark LVI, com docs, specs, memória, skills e versão próprios. |
| D2 | Metodologia (2026-10-16) | **Spec Kit** para toda feature (`specs/NNN-nome/`), constituição em `.specify/memory/constitution.md`. GSD não é usado neste projeto. |
| D3 | Branches e PRs (2026-10-16) | Feature = branch `NNN-nome` a partir de `irlan-dev` + PR para `irlan-dev`. Manutenção: `fix/…`, `chore/…`, `docs/…`. `main` só espelha o upstream. |
| D4 | Navegação no código (2026-10-16) | **graphify** é o primeiro recurso para perguntas sobre o código (`graphify query/path/explain`), e o grafo é atualizado depois de cada mudança (`graphify update .`). |
| D5 | Modelos no desenvolvimento (2026-10-16) | Especificar, esclarecer, planejar e revisar ficam com o modelo mais forte da sessão. Tarefas já planejadas vão para agentes do projeto com modelo mais barato: `lv-implementer` (Sonnet) e `lv-quick` (Haiku). Revisão independente com `lv-reviewer`. Ver `HARNESS.md`. |
| D6 | Versão (2026-10-16) | Versionamento próprio `55.MINOR.PATCH` (55 = Mark LV), registrado no `CHANGELOG.md` e em tags `v55.x.y` em `irlan-dev`. Cada feature mesclada sobe o MINOR; correção sobe o PATCH. |
| D7 | Memória do projeto (2026-10-16) | A memória que acompanha o repositório é: esta `VISAO.md` (decisões), `docs/conversas/` (resumo das conversas que geraram decisões), a constituição e as specs. A memória automática do Claude Code fica na máquina e não substitui estes arquivos. |
| D8 | Integrações pessoais (2026-10-16) | Obsidian, Agenda, Gmail, Notion e Telegram entram como **plugins** (`plugins/`), não como ações, para manter `actions/` igual ao upstream. |
| D9 | Banco da memória (2026-10-16, proposta) | Migrar `long_term.json` para **SQLite com FTS5** (biblioteca padrão), mantendo o desenho de núcleo no prompt + busca sob demanda. Postgres/Docker fica fora do LV. A confirmar na spec 004. |
| D10 | Modelo do JARVIS (2026-10-16, a decidir pelo Irlan) | Ligar a cobrança da chave Gemini (sem mudar código) para sair da cota curta e do uso dos dados para treino; medir o custo de uma hora de voz antes de fixar o teto. Outros provedores só para código, via Claude Code. |

## 5. Roteiro
Ordem pensada para que cada passo destrave o seguinte. Números de spec são **previstos** (o Spec
Kit numera na criação). Parecer e dependências de cada item em [`demandas.md`](demandas.md).

| Ordem | Spec | Feature | Por que nesta posição | Modelo sugerido p/ implementar |
|---|---|---|---|---|
| 0 | — | Estrutura de desenvolvimento (este PR) | Tudo o que vem depois usa | — |
| 1 | 001 | Registro de erros + `report_problem` + base de `pytest` | Sem registro não se mede nem se corrige nada | Sonnet |
| 2 | 002 | Aviso de espera garantido ("só um momento") | Ganho imediato de confiança; usa o registro para medir latência | Sonnet |
| 3 | 003 | Pastas liberadas (política central de arquivos) | Fundação de auditoria, Obsidian e organização | Sonnet |
| 4 | 004 | Memória em SQLite (FTS5, migração, backup) | Robustez; base de Telegram e rotinas | Sonnet |
| 5 | 005 | Voz: diálogo afetivo quando suportado + jeito | Pequeno; testar cedo com a chave paga | Haiku/Sonnet |
| 6 | 006 | Cartões no HUD (progresso, pesquisa, notícias) | Diferencial visual; os agentes vão usar | Opus no plano, Sonnet nas tarefas |
| 7 | 007 | Auditoria de projetos (delegação ao Claude Code) | Precisa de 003; mostra resultado em cartão | Sonnet |
| 8 | 008 | Plugin Obsidian | Precisa de 003 | Sonnet |
| 9 | 009 | Plugins Google Agenda, depois Gmail (leitura) | Uso diário; e-mail com cuidado de injeção | Sonnet |
| 10 | 010 | Agentes especialistas (pesquisa, código) | Reaproveita 006 e 007 | Opus no plano, Sonnet nas tarefas |
| 11 | 011 | Bot do Telegram (com o PC ligado) | Precisa de 004; limite estrutural assumido | Sonnet |
| 12 | 012 | Rotinas e particularidades | Precisa de 001 e 004 com dados acumulados | Opus no plano |
| 13 | 013 | Notion | Só se o uso se confirmar | Sonnet |

## 6. Em aberto
- **D10:** ligar a cobrança da chave Gemini e definir o teto mensal (passo do Irlan).
- Caminho e convenções do cofre Obsidian (levantar na spec 008).
- Se o Notion é usado de fato (decide se a spec 013 existe).
- Criar as tags `v55.0.0` (base do upstream) e `v55.1.0` (trabalho do fork até `a56cb2a`) e enviá-las
  ao `origin` — aguardando confirmação.
