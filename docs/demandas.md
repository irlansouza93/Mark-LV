# Demandas do Mark LV — estado atual e parecer crítico

> Levantamento de 2026-10-16 sobre o código do fork (`irlan-dev`, commit `a56cb2a`). Para cada
> demanda: **o que existe hoje**, **parecer** (o que pesa a favor e contra, riscos) e
> **recomendação** (como fazer dentro da estrutura atual do LV). A ordem de execução está em
> [`VISAO.md` §5](VISAO.md#5-roteiro). Cada item vira uma spec do Spec Kit quando chegar a vez.
>
> Esforço: **P** (até um dia), **M** (alguns dias), **G** (uma semana ou mais).

## Resumo

| # | Demanda | Hoje | Parecer em uma linha | Esforço | Spec prevista |
|---|---|---|---|---|---|
| 1 | Plugins | Sistema pronto, nenhum plugin | Usar como casa das integrações pessoais | P | junto de cada integração |
| 2 | Pastas liberadas | Só a pasta do usuário, sem escolha | Base de tudo que mexe em arquivo; fazer cedo | M | 003 |
| 3 | Auditar projetos | Um arquivo por vez | Delegar a quem lê repositório inteiro (Claude Code) | M | 011 |
| 4 | Obsidian / Notion | Nada | Os dois, com papéis diferentes: Notion opera, Obsidian guarda 10 anos ([análise](gestao-pessoal.md)) | M + M | 006, 007, 008, 009 |
| 5 | Voz mais humana | Áudio nativo, afetivo desligado | Ganho real é pequeno e barato; testar, não reescrever | P | 013 |
| 6 | LLM pago | Gemini grátis com escada | Grátis continua a base; DeepSeek/OpenAI por roteador ([análise](llm-gestao.md)) | M | 001 (medir) + 004 |
| 7 | Gmail / Agenda | Nada | Agenda primeiro; e-mail só lendo, envio por último | M + M | 012 |
| 8 | Telegram com memória | Só envia, clicando no app | Possível só com o PC ligado; é o limite do LV | G | 015 |
| 9 | Banco e memória | JSON + palavra-chave | Migrar para SQLite (FTS5); sem Postgres aqui | M | 005 |
| 10 | Aprender rotinas | Nada | Não é "treinar modelo": é registrar e resumir | M | 016 |
| 11 | Subagentes | Mecanismo existe, sem agentes | Dois especialistas com uso real, não um zoológico | M | 014 |
| 12 | Cartões no HUD | Só vídeo no centro | Maior diferencial do LV; vale o investimento | G | 010 |
| 13 | "Só um momento" | Existe no prompt | Falta a garantia que não depende do modelo | P | 002 |
| 14 | Log de erros | Só console | Primeira coisa a fazer; alimenta todas as outras | P | 001 |

---

## 1. Plugins

**Hoje.** O criador deixou o sistema aberto e pronto, não um catálogo: um arquivo `.py` em
`plugins/` com o dicionário `PLUGIN` e `run()` ([`plugins/_template.py`](../plugins/_template.py)),
descoberto ao abrir, liga/desliga em 🧩 PLUGINS sem reiniciar, com campos de configuração
(`PLUGIN_SETTINGS`), `NON_BLOCKING` e `plugin_say` para falar no meio da tarefa. **Não existe
repositório de plugins**, nem do criador nem da comunidade, e **não temos nenhum plugin**. A regra
dele: `actions/` é o que mexe no computador; o resto (hobby, ofício, serviço) é plugin. O
`.gitignore` do upstream já reserva tokens OAuth "dos plugins Gmail/Calendar", sinal de que ele
pensou nisso, mas nunca publicou.

**Parecer.** O desenho é bom e barato de usar. Os pontos fracos: (a) todo plugin ligado vai para o
modelo em toda conexão, sem limite; (b) plugin roda com todos os poderes do processo, sem
classificação de risco; (c) não há metadados de versão nem de dependências; (d) nada testa um
plugin antes de ele ir para o ar.

**Recomendação.** Fazer de `plugins/` a casa de **todas** as integrações pessoais (Obsidian,
Agenda, Gmail, Notion, Telegram): mantém `actions/` igual ao upstream e facilita o merge. Ao
escrever o primeiro, acrescentar ao contrato (sem quebrar o atual): `risk` (leitura/reversível/
irreversível) e `requires` (pacotes), e um teste mínimo por plugin. A skill `lv-new-skill` guia a
escolha entre ação e plugin.

## 2. Acesso a pastas do disco (pastas liberadas)

**Hoje.** [`actions/file_controller.py:93`](../actions/file_controller.py) só aceita caminhos dentro
de `Path.home()` (`C:\Users\irlan`). **`C:\DESENVOLVIMENTO` fica fora**, e não há como liberar outra
pasta. Apagar vai para a lixeira, pastas críticas são protegidas e há desfazer. Mas a regra vale
**só nesse arquivo**: `code_helper` lê qualquer caminho sem conferir nada, e `desktop.py` roda
código escrito pelo modelo.

**Parecer.** É a fundação de três demandas (auditar projetos, Obsidian, organizar arquivos). Hoje
a segurança é ao mesmo tempo restritiva demais (não alcança seus projetos) e frouxa demais (outras
ações passam por fora). O roadmap do criador fala em "full file access", que seria o erro oposto.

**Recomendação.** Um módulo central (`core/fs_policy.py`) com a lista de pastas liberadas e o modo
de cada uma (só leitura / leitura e escrita), editável em ⚙ SETUP, e uma lista de negação fixa
(`config/api_keys.json`, `.ssh`, cofres de senha, `.git/` interno). Todas as ações que tocam disco
passam por ele. Testável por `pytest` sem interface.

## 3. Entrar em projetos, ler, auditar e sugerir melhorias

**Hoje.** `code_helper` escreve, edita, explica e roda **um arquivo**; `dev_agent` cria projetos
novos em `Desktop/JarvisProjects`. Nada percorre um repositório.

**Parecer.** Fazer isso dentro da sessão de voz é má ideia: a sessão Live tem janela de contexto
limitada, cada token de código lido é pago em toda a conversa, e o modelo de áudio não é o melhor
revisor de código. Construir "o nosso Claude Code" também seria desperdício.

**Recomendação.** O JARVIS **orquestra** e delega: uma ação `code_review` (`NON_BLOCKING`) que roda
o Claude Code sem tela (`claude -p`, somente leitura, dentro de uma pasta liberada) ou uma chamada
de contexto longo do Gemini, salva o relatório em Markdown, resume em voz e mostra em cartão no HUD
(demanda 12). Alterações vêm depois e sempre em branch + PR, nunca direto. Depende da demanda 2.

## 4. Obsidian e Notion

**Hoje.** Nenhum dos dois integrado. Existe o cofre `C:\DESENVOLVIMENTO\OBSIDIAN` (estrutura PARA,
sincronizado por git, desenhado para durar 10 anos) e uma conta no Notion.

**Parecer (revisto em 2026-10-16).** A primeira versão deste parecer tratou o Notion como opcional —
errado: o objetivo é **gestão pessoal completa** (tarefas, rotina, projetos, estudos, finanças), e
para isso os dois se complementam. O Notion é melhor para dado estruturado que muda todo dia; o
Obsidian é o que garante a memória de 10 anos em Markdown puro. Usar só um força o outro papel a
funcionar mal.

**Recomendação.** Notion como bancada operacional, Obsidian como arquivo permanente, e o JARVIS
destilando um no outro. Análise completa, riscos e ordem em
[`gestao-pessoal.md`](gestao-pessoal.md).

## 5. Voz mais humana e com emoção

**Hoje.** Áudio nativo do Gemini Live (a voz nasce no modelo: resposta rápida e interrompível),
30 vozes com amostra e o painel VOZ E JEITO. O **diálogo afetivo foi desligado** porque o
`gemini-3.1-flash-live` não o aceita ([`main.py:1094`](../main.py)). O `core/tts.py` (ElevenLabs,
Kokoro, Edge) é sobra de versões antigas; nada o usa.

**Parecer.** O áudio nativo já é o caminho mais humano em latência. Trocar para um TTS externo
(ElevenLabs) daria timbre mais expressivo, mas somaria o tempo de gerar áudio a cada frase e
quebraria a interrupção natural: pior conversa. O ganho realista vem do diálogo afetivo (quando
algum modelo Live em uso aceitar) e do "jeito" no prompt, ambos baratos.

**Recomendação.** Spec pequena: preferência `affective_dialog` que só liga se o modelo aceitar
(conferir com chamada real), mais um teste de escuta lado a lado. Remover o `core/tts.py` morto
fica a critério (é do upstream; apagar dificulta merge).

## 6. Modelos de LLM (grátis, pagos e créditos)

**Hoje.** Só Gemini grátis, com a escada de 9 modelos de [`core/gemini.py`](../core/gemini.py) — que
o criador desenhou justamente para o nível gratuito (sessão Live descartável na frente, porque o
pool do Live é separado do pool de texto que esgota). `core/llm_client.py` (Ollama/LM Studio) é
sobra sem uso.

**Parecer (revisto em 2026-10-16).** A primeira versão recomendava ligar a cobrança do Gemini
sem ter avaliado o grátis — errado. O grátis funciona bem para o uso atual; o que falta é (a) medir
quando ele esgota, (b) uma reserva barata quando esgotar, e (c) um destino privado para dado
sensível, porque no grátis o Google pode usar o conteúdo para treino. Os créditos de DeepSeek e
OpenAI resolvem (b) e (c) por centavos por mês.

**Recomendação.** Medir primeiro (spec 001), depois um roteador por tipo de tarefa e sensibilidade
(spec 004). Análise completa, preços e a decisão pendente sobre voz + dado sensível em
[`llm-gestao.md`](llm-gestao.md).

## 7. Gmail, Agenda (calendário)

**Hoje.** Nada. Os lembretes usam o Agendador de Tarefas do Windows (`actions/reminder.py`).

**Parecer.** Útil todo dia, mas com dois cuidados: (a) **e-mail é o vetor de ataque mais barato**
contra um assistente com mãos (um e-mail pode "pedir" coisas; Princípio V); (b) o OAuth do Google
para app próprio em modo de teste **expira o token a cada 7 dias**, exigindo novo login — publicar
o app ou aceitar o relogin semanal. Enviar e-mail é irreversível (confirmação real).

**Recomendação.** Plugin `google_calendar` primeiro (ler a agenda, criar evento com desfazer,
briefing do dia). Depois `gmail` só leitura (resumir, buscar, rascunho); enviar por último, sempre
com confirmação. Tokens em `config/` (já ignorados pelo git).

## 8. Telegram com acesso às memórias

**Hoje.** `send_message` só **envia**, automatizando o app de desktop por cliques (frágil). O
`dashboard/` é um controle remoto pelo celular na rede local. "Telegram remote" está no roadmap
do criador.

**Parecer.** Dá para fazer no LV um bot do Telegram (API oficial, polling) rodando dentro do
JARVIS, conversando por texto com acesso à mesma memória. **Limite estrutural:** só funciona com o
PC ligado e o JARVIS aberto. Ter o assistente 24h é justamente o que exigiu a arquitetura do LVI;
aqui seria um canal "enquanto estou no PC ou ele está ligado". Também exige um caminho de texto
(chamada one-shot com ferramentas) separado da sessão de áudio.

**Recomendação.** Fazer depois da memória em SQLite, com lista fechada de `chat_id`, só leitura de
memória e lembretes no início, e nenhuma ação irreversível pelo Telegram sem confirmação na tela.

## 9. Banco de dados e memória mais robustos

**Hoje.** [`memory/memory_manager.py`](../memory/memory_manager.py): um JSON (`long_term.json`) com
trava de thread, limite de 200 mil caracteres, núcleo de 900 caracteres no prompt, índice de chaves
e busca por palavra-chave (`recall_memory`), mais resumo por sessão. A gravação **não é atômica**
(`write_text` direto): uma queda no meio da escrita corrompe a memória inteira. Não há backup.

**Parecer.** O desenho do criador (núcleo pequeno no prompt + busca sob demanda) está certo e deve
ficar. O que falta é robustez e busca por sentido. Postgres/pgvector seria exagero para um app de
desktop de um usuário. O `.gitignore` do upstream já prevê `memory/*.db`.

**Recomendação.** SQLite (biblioteca padrão do Python, zero dependência) com FTS5 para busca de
texto, origem e data de cada fato, migração automática do JSON, backup diário rotativo. Busca
semântica (embeddings do Gemini guardados no próprio SQLite) numa segunda etapa, se a busca de
texto não bastar.

## 10. Aprender rotinas e particularidades

**Hoje.** Nada aprende. O `ProactiveEngine` decide quando falar por hora do dia e silêncio; a
memória guarda fatos que o modelo resolve salvar.

**Parecer.** "Aprendizado de máquina" no sentido de treinar um modelo não é a ferramenta certa:
um usuário gera poucos dados, e um modelo treinado seria opaco e difícil de corrigir. O que funciona
é **registrar eventos** (o que você pediu, quando, qual app, qual resultado), **contar padrões**
simples (hora, dia da semana, sequência) e, periodicamente, **pedir ao LLM que resuma** em fatos
de rotina com nível de confiança, que você vê e apaga no painel de memória.

**Recomendação.** Depende do registro de eventos (demanda 14) e do SQLite (demanda 9). Começa
passivo (só observa e mostra "percebi que…"), e só depois alimenta o proativo.

## 11. Subagentes para delegar tarefas

**Hoje.** Não há agentes, mas o mecanismo existe: ferramentas `NON_BLOCKING` com `scheduling` e
`plugin_say` para narrar progresso. A sessão de voz já é, na prática, a orquestradora.

**Parecer.** Multiagente multiplica custo e pontos de falha; vale quando um especialista faz algo
que a sessão de voz faz mal. Hoje há dois casos claros: **pesquisa** (várias buscas e síntese
longa) e **código** (demanda 3).

**Recomendação.** Um padrão único de "agente" em `core/` (laço de ferramentas one-shot com teto de
passos, tempo e custo, eventos de progresso) e dois agentes reais. Progresso visível em cartões
(demanda 12).

## 12. Canvas do holograma com dados reais (estilo JARVIS / óculos Meta)

**Hoje.** O centro do HUD alterna cabeça, câmera e vídeo; os painéis são de configuração. Nenhum
resultado aparece na tela.

**Parecer.** É o que mais diferencia o LV, e o motor de holograma do fork já é forte. O risco é
técnico (orçamento de 15 ms por quadro, não poluir o visual) e de desenho (o que mostrar e por
quanto tempo). Mapas: o Google Maps estático exige faturamento no Cloud; tiles do OpenStreetMap
têm política de uso, mas servem para uso pessoal leve.

**Recomendação.** As ferramentas passam a devolver, opcionalmente, um **cartão** (tipo + campos,
nunca HTML), que a `JarvisUI` recebe por sinal e desenha em volta do holograma com o mesmo estilo
de luz. Primeiros tipos: progresso de ação, resultado de pesquisa, resumo de notícias. Lugares e
mapa depois.

## 13. "Só um momento, vou buscar"

**Hoje.** **Já existe**: a regra `[ACKNOWLEDGE BEFORE A SILENCE]` do prompt
([`core/prompt.txt:73`](../core/prompt.txt)), do Mark LIII, manda dizer uma frase específica antes
de qualquer espera; ferramentas lentas podem ser `NON_BLOCKING`.

**Parecer.** Depende de o modelo obedecer, e às vezes ele não obedece — principalmente quando a
demora é para *escrever* os argumentos da ferramenta. Não há rede de segurança.

**Recomendação.** Uma garantia determinística: se o turno do usuário terminou e nenhum áudio do
modelo chegou em ~1,5 s (valor a medir), o HUD entra em estado "pensando" (rosto/cor) e, passado
outro limite, toca um som curto local. Medir antes quanto tempo leva hoje cada tipo de pedido.

## 14. Log de erros com autorrelato, entrando no plano de correção

**Hoje.** Erros vão para `print`/`traceback.print_exc()` no console e para o log da tela; somem ao
fechar. Ações e plugins que quebram são isolados (o app não cai), mas nada fica guardado. `logs/`
já está no `.gitignore`.

**Parecer.** É a base de quase tudo acima: sem registro, não dá para saber o que quebrou, medir
latência (13) nem aprender rotina (10). E é pequeno.

**Estado (2026-10-18): implementado na spec 001** (`specs/001-error-log-llm-usage/`): `logs/` com
console, falhas agrupadas por assinatura, relatos (`report_problem` + relato automático) e uso de
LLM com custo estimado; `tools/usage_report.py` resume. O que a medição real mostrou (sessão de voz
de 3,5 min, `gemini-3.1-flash-live-preview`): cada turno reenvia **~17 mil tokens fixos de texto**
(prompt do sistema + declarações das ferramentas), então um turno custa **US$ 0,009–0,024 se fosse
pago** — o custo de uma conversa vem do contexto, não da fala. A medição de duas semanas segue
rodando; a decisão de custo (D10) usa esses números.

**Recomendação.** `logging` com arquivo rotativo em `logs/` mais um registro estruturado (JSON
Lines) de falhas: ação, erro, contexto, versão (commit), com filtro de segredos. Uma ferramenta
`report_problem` para quando você disser "não funcionou" (o JARVIS anota o que você esperava).
Falhas iguais agrupadas. Na mesma spec, o **uso de LLM** (provedor, tokens, custo, 429) também é
registrado: é o dado que decide a gestão de modelos ([`llm-gestao.md`](llm-gestao.md) §6). Uma skill do Claude Code (`lv-triage`) lê o registro e propõe issues ou
specs, e você confirma antes de abrir qualquer issue.
