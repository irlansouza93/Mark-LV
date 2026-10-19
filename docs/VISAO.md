# Mark LV (fork irlansouza93) — Visão e decisões

> Fonte da verdade para "o quê e por quê" deste fork. Atualize quando uma decisão mudar, com data.
> O "como" está na [constituição](../.specify/memory/constitution.md); o parecer de cada demanda em
> [`demandas.md`](demandas.md); o funcionamento do ambiente de desenvolvimento em
> [`HARNESS.md`](HARNESS.md). Origem: [conversa de 2026-10-16](conversas/2026-10-16-estrutura-lv.md).

## 1. A ideia em uma frase
Um **JARVIS de desktop que já funciona**: holograma no HUD, conversa por voz em tempo real pelo
Gemini Live e mãos no computador. O fork evolui essa base, sem reescrever a arquitetura, até ela
ler e cuidar dos seus projetos, notas e agenda, **organizar a vida pessoal** (tarefas, rotina,
estudos, finanças) com Notion e o cofre Obsidian de 10 anos, mostrar na tela o que está fazendo e
aprender com o uso — gastando o mínimo em LLM.

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
| D9 | Banco da memória (2026-10-16, proposta) | Migrar `long_term.json` para **SQLite com FTS5** (biblioteca padrão), mantendo o desenho de núcleo no prompt + busca sob demanda; busca por sentido com embeddings privados (OpenAI `text-embedding-3-small`) numa segunda etapa. Postgres/Docker fica fora do LV. A confirmar na spec da memória. |
| D10 | Gestão de LLMs (2026-10-16, revista no mesmo dia) | **Gemini grátis continua sendo a base** (voz e chamadas avulsas, como o criador desenhou). DeepSeek e OpenAI (créditos do Irlan) entram por um **roteador por tipo de tarefa e sensibilidade**: reserva barata quando o grátis esgota, DeepSeek para contexto longo não sensível, OpenAI para dado pessoal sensível e embeddings, com teto mensal por provedor. Cobrança do Gemini **não** é ligada agora; decisão só depois de medir o uso (spec 001). Análise: [`llm-gestao.md`](llm-gestao.md). |
| D11 | Gestão pessoal (2026-10-16, proposta) | **Notion = bancada operacional** (tarefas, projetos, rotinas, cursos em andamento, finanças) e **Obsidian = memória de 10 anos** (diário, conhecimento, histórico estudantil, fechamentos mensais). O JARVIS captura, consulta e **destila** periodicamente o Notion para o Obsidian; cada dado tem um único dono. Análise: [`gestao-pessoal.md`](gestao-pessoal.md). |
| D12 | Voz e dado sensível: **modo privado** (2026-10-16, decidido pelo Irlan) | Um **segundo projeto Google, com cobrança ligada e teto de gasto baixo**, com chave própria, usado **só quando o assunto é sensível** (finanças, saúde, dados pessoais). Fora disso a voz segue no projeto grátis. A sessão Live reconecta com a chave privada ao entrar no modo (por comando de voz, botão no HUD ou automaticamente quando uma ferramenta sensível é chamada) e volta ao grátis ao sair; o HUD mostra claramente quando o modo privado está ativo. No modo privado, nenhuma chamada sensível cai no Gemini grátis nem no DeepSeek. Detalhes em [`llm-gestao.md` §5](llm-gestao.md#5-voz--dado-sensível-modo-privado-d12). |

## 5. Roteiro
Ordem pensada para que cada passo destrave o seguinte. Números de spec são **previstos** (o Spec
Kit numera na criação). Parecer e dependências de cada item em [`demandas.md`](demandas.md).

| Ordem | Spec | Feature | Por que nesta posição | Modelo sugerido p/ implementar |
|---|---|---|---|---|
| 0 | — | Estrutura de desenvolvimento (este PR) | Tudo o que vem depois usa | — |
| 1 | 001 | Registro de erros + **medição de uso de LLM** (tokens, custo, 429 por provedor) + `report_problem` + base de `pytest` | Sem registro não se mede, não se corrige e não se decide custo | Sonnet |
| 2 | 002 | Aviso de espera garantido ("só um momento") | Ganho imediato de confiança; usa o registro para medir latência | Sonnet |
| 3 | 003 | Pastas liberadas (política central de arquivos) | Fundação de Obsidian, auditoria e organização | Sonnet |
| 4 | 004 | Roteador de LLMs (Gemini grátis → DeepSeek/OpenAI; classe sensível; tetos) + **modo privado** da voz (D12) | Pré-requisito de privacidade e economia para tudo que vem depois (D10, D12) | Opus no plano, Sonnet nas tarefas |
| 5 | 005 | Memória em SQLite (FTS5, migração, backup; embeddings privados depois) | Robustez; base de Notion, Telegram e rotinas | Sonnet |
| 6 | 006 | Plugin Obsidian (diário, inbox, notas de estudo nos modelos do cofre) | Gestão pessoal (D11); precisa de 003 | Sonnet |
| 7 | 007 | Plugin Notion (criar bases; tarefas, projetos, rotinas, cursos) | Gestão pessoal (D11) | Sonnet |
| 8 | 008 | Finanças no Notion (lançamento com confirmação, orçamento, metas, relatório) | Depende de 004 (modo privado) e 007 | Opus no plano, Sonnet nas tarefas |
| 9 | 009 | Destilação Notion → Obsidian + briefing de bom dia / boa noite | Fecha o ciclo da memória de 10 anos | Sonnet |
| 10 | 010 | Cartões no HUD (progresso, dia, orçamento, pesquisa, notícias) | Diferencial visual; mostra o que 006–009 produzem | Opus no plano, Sonnet nas tarefas |
| 11 | 011 | Auditoria de projetos (delegação ao Claude Code) | Precisa de 003; resultado em cartão | Sonnet |
| 12 | 012 | Plugins Google Agenda, depois Gmail (leitura) | Uso diário; e-mail com cuidado de injeção | Sonnet |
| 13 | 013 | Voz: diálogo afetivo + teste do Gemini 3.8 Live | Pequeno; pode subir na fila a qualquer momento | Haiku/Sonnet |
| 14 | 014 | Agentes especialistas (pesquisa, código) | Reaproveita 004, 010 e 011 | Opus no plano, Sonnet nas tarefas |
| 15 | 015 | Bot do Telegram (com o PC ligado) | Precisa de 005; limite estrutural assumido | Sonnet |
| 16 | 016 | Rotinas e particularidades aprendidas | Precisa de 001, 005 e dados acumulados | Opus no plano |

## 6. Em aberto
- **D12:** criar o projeto Google privado com cobrança e teto (passo do Irlan, quando a spec 004 estiver pronta); confirmar o teto inicial sugerido de US$ 5/mês.
- **D10:** tetos mensais de DeepSeek e OpenAI e a validade dos créditos (conferir nos painéis); revisar depois de duas semanas de medição (spec 001). **A medição começou em 2026-10-17** (`tools/usage_report.py`); primeiro dado: o Live reenvia ~17 mil tokens fixos de texto por turno, ≈ US$ 0,009–0,024 por turno se fosse pago (7 turnos, sessão de 3,5 min).
- **D11:** bases e campos de cada área no Notion (tarefas, rotinas, cursos, finanças) — levantar no `/speckit-clarify` da spec 007.
- Criar as tags `v55.0.0` (base do upstream) e `v55.1.0` (trabalho do fork até `a56cb2a`) e enviá-las ao `origin` — aguardando confirmação.
