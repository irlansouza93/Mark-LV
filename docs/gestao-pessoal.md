# Gestão pessoal com Notion + Obsidian (memória de 10 anos) — viabilidade

> Análise de 2026-10-16. Objetivo do Irlan: controlar pelo JARVIS aprendizado, rotina diária,
> tarefas, projetos, vida estudantil (cursos atuais, graduações feitas e em andamento, histórico),
> finanças (orçamento mensal, gastos, ganhos, rendimentos, investimentos, metas) e rotinas
> repetidas — usando o **Notion** junto com o **cofre Obsidian de 10 anos**.

## 1. O que já existe

- **Cofre Obsidian** em `C:\DESENVOLVIMENTO\OBSIDIAN`, criado em 2026-10-12: estrutura PARA
  (`00_Inbox`, `01_Diario`, `10_Projetos`, `20_Areas`, `30_Conhecimento`, `90_Arquivo`, `_anexos`),
  frontmatter com `tags`, `tipo`, `data_criacao`, e sincronização por **git** (plugin
  `obsidian-git`, repositório `irlansouza93/obsidian-vault`). Ainda com poucas notas: é o melhor
  momento para definir modelos.
- Os princípios do próprio cofre ("Guia e Fundamentos para 10 Anos"): **Markdown puro e
  independente de software**, conexões acima de pastas, notas atômicas.
- **Notion:** você tem conta; nada integrado.
- **No LV:** nenhuma integração com nenhum dos dois.

## 2. A tensão central

O seu princípio nº 1 do cofre é "daqui a 10 anos, qualquer editor abre minhas notas". O Notion é o
oposto: nuvem de terceiro, formato próprio, exportação imperfeita. Mas o Notion é muito melhor em
**dados estruturados que mudam todo dia** (tarefas com status, transações, cursos com progresso,
hábitos marcados), com visões, filtros, fórmulas e captura rápida no celular — coisas que em
Markdown ficam trabalhosas.

Usar só um dos dois força o outro papel a funcionar mal. A proposta é dividir pelo **tipo de dado**
e deixar o JARVIS fazer a ponte.

## 3. Proposta: Notion é a bancada, Obsidian é o arquivo

| | **Notion (bancada operacional)** | **Obsidian (memória de 10 anos)** |
|---|---|---|
| Natureza | Estruturado, muda todo dia, tem status | Narrativo, permanente, ligado por conceitos |
| Tarefas e rotinas | Base Tarefas (status, prazo, recorrência), Base Rotinas/Hábitos (checklist diário) | O diário registra o que foi feito e aprendido |
| Projetos | Base Projetos (status, prazo, tarefas relacionadas) | Nota do projeto em `10_Projetos/` → `90_Arquivo/` ao concluir, com retrospectiva |
| Estudos | Base Cursos (instituição, carga, progresso, prazos, provas) | Nota permanente por graduação/curso em `20_Areas/Estudos/` (histórico, certificados, o que aprendeu); notas de aula em `30_Conhecimento/` |
| Aprendizado | Fila "para estudar" | As notas de conhecimento em si |
| Finanças | Bases Transações, Orçamento mensal, Contas/Investimentos, Metas | Fechamento mensal em `20_Areas/Financas/` (resumo, decisões, evolução do patrimônio) |
| Diário | — | `01_Diario/AAAA-MM-DD.md` |

**A regra que preserva os 10 anos — destilação:** periodicamente o JARVIS lê o Notion e escreve no
Obsidian o que precisa durar.
- **Diária:** a nota do diário recebe as tarefas concluídas, os hábitos marcados e o que você
  contou ao JARVIS.
- **Mensal:** "Fechamento 2026-10" com o resumo financeiro (gastos por categoria, ganhos,
  rendimentos, metas), cursos concluídos e projetos encerrados.
- **Ao concluir** um curso/graduação/projeto: nota permanente com o histórico.

Assim, se o Notion sumir ou você trocar de ferramenta, a memória de 10 anos está inteira no
Obsidian; o Notion guarda só o "andamento".

## 4. Papel do JARVIS (o que fica possível por voz)

- **Capturar:** "anota que preciso renovar a matrícula até sexta" → tarefa no Notion; "aprendi
  hoje que…" → inbox/diário no Obsidian; "gastei 80 no mercado" → transação no Notion
  (**com confirmação**; ver §6).
- **Consultar:** "o que tenho hoje?", "quanto gastei com comida este mês?", "em que módulo estou do
  curso X?", "o que eu anotei sobre Docker?".
- **Briefing de bom dia e fechamento de boa noite:** tarefas do dia, rotinas, prazos de curso,
  orçamento restante; à noite, o que foi feito vira diário.
- **Cartões no HUD:** a lista do dia, o orçamento do mês, o progresso do curso aparecem na tela
  enquanto ele fala (spec de cartões).
- **Rotinas repetidas:** a base Rotinas do Notion define o que se repete; o JARVIS lembra, marca e,
  com o tempo (spec de rotinas aprendidas), sugere ajustes.

## 5. Viabilidade técnica

**Notion — viável e barato.**
- API oficial gratuita com integração interna (token), funciona no plano grátis. Limite médio de
  **~3 requisições/segundo** por integração: suficiente para uso pessoal, exige fila e cache.
- A API mudou o modelo de bases em 2025 ("data sources"); o plugin deve fixar a versão da API.
- O JARVIS pode **criar as bases** com o esquema certo na primeira configuração (não precisa montar
  à mão), e só enxerga as páginas compartilhadas com a integração.
- Dependência: nenhuma nova (o LV já usa `requests`).

**Obsidian — viável e mais simples ainda.**
- É uma pasta de `.md`: o JARVIS lê e escreve arquivos, dentro da pasta liberada (spec 003), com
  desfazer, seguindo os modelos e o frontmatter do cofre.
- Cuidado com o `obsidian-git`: gravar de forma atômica (arquivo temporário + renomear) para não
  ser commitado pela metade.
- Busca: começar pela busca de texto; depois embeddings privados (ver `llm-gestao.md`).

**Custo de LLM:** classificar/extrair cada captura custa frações de centavo; a destilação mensal,
alguns centavos — desde que vá para o provedor certo (§6).

## 6. Riscos e decisões

1. **Privacidade da voz (a maior).** Finanças e saúde ditas por voz passam pelo Gemini Live; no
   nível grátis isso pode virar treino do Google. Opções em
   [`llm-gestao.md` §5](llm-gestao.md#5-o-ponto-que-exige-decisão-voz--dado-sensível) (modo privado
   com chave paga só nesses momentos, ou sensível só por texto).
2. **Dinheiro no Notion:** números do Notion são ponto flutuante; para uso pessoal serve, mas
   valores devem ser gravados em centavos inteiros ou com 2 casas fixas, e o fechamento mensal
   confere a soma. Rendimentos de investimento entram **por lançamento** (você ou extrato), não por
   integração bancária.
3. **Fonte da verdade duplicada:** cada tipo de dado tem **um** dono (tabela do §3). O JARVIS nunca
   edita a mesma informação nos dois lugares; o Obsidian recebe resumo, não cópia viva.
4. **Dependência do Notion:** mitigada pela destilação; e o próprio JARVIS pode exportar as bases
   para CSV no cofre (`90_Arquivo/notion-export/`) uma vez por mês.
5. **Limite estrutural do LV:** tudo isso funciona com o PC ligado e o JARVIS aberto. Captura pelo
   celular fica com o app do Notion (que já existe) e o JARVIS processa quando abrir.

## 7. Como entra no roteiro (detalhe em [`VISAO.md` §5](VISAO.md#5-roteiro))

1. Roteador de LLM com classe "sensível" (pré-requisito de privacidade).
2. **Plugin Obsidian:** diário, inbox, notas de estudo com os modelos do cofre.
3. **Plugin Notion:** criar as bases, tarefas, projetos, rotinas, cursos (ler, criar, atualizar).
4. **Finanças no Notion:** transações por voz/texto com confirmação, orçamento, metas, relatório.
5. **Destilação e briefings:** diário automático, fechamento mensal, bom dia / boa noite.

Antes da spec do Notion: listar com você as bases e campos de cada área (uma conversa de
`/speckit-clarify`), e decidir a questão de privacidade da voz.
