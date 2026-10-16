# 2026-10-16 — Estrutura de desenvolvimento própria do Mark LV

**Contexto.** O Irlan trouxe 14 demandas para a evolução do JARVIS (plugins, pastas liberadas,
auditoria de projetos, Obsidian/Notion, voz mais humana, LLM pago, Gmail/Agenda, Telegram com
memória, banco e memória robustos, aprender rotinas, subagentes, cartões no HUD estilo JARVIS/Meta,
"só um momento" e log de erros com autorrelato) e pediu um parecer crítico de cada uma.

**Correção de rumo.** Na primeira resposta, o Claude registrou três ideias no `VISAO.md` do
**Mark LVI**, achando que o LV estava congelado. O Irlan corrigiu: o LVI é outro projeto, com outra
infraestrutura; o **Mark LV continua evoluindo na estrutura atual**, e o trabalho do LV não toca no
repositório do LVI. A edição no LVI foi desfeita (não tinha sido commitada).

**Decisões** (detalhe em [`VISAO.md` §4](../VISAO.md#4-decisões-tomadas)):
- LV com docs, specs, memória, skills e versão próprios (D1, D6, D7).
- Spec Kit + graphify + harness do Claude Code (D2, D4) — ver [`HARNESS.md`](../HARNESS.md).
- Features em branches `NNN-nome` com PR para `irlan-dev` (D3).
- Tarefas já planejadas podem rodar em modelos mais baratos do Claude (D5).
- Integrações pessoais como plugins (D8); memória em SQLite como proposta (D9).

**Parecer das demandas:** [`demandas.md`](../demandas.md). Roteiro: [`VISAO.md` §5](../VISAO.md#5-roteiro).

**Ficou para o Irlan:** ligar a cobrança da chave Gemini (D10); confirmar a criação das tags de
versão; dizer se usa Notion de fato; caminho do cofre Obsidian.
