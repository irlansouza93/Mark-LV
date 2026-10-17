# Mark LV Constitution (fork irlansouza93)

> Regras não negociáveis de **como** o Mark LV é evoluído neste fork. O **quê e por quê** vive em
> [`docs/VISAO.md`](../../docs/VISAO.md); o parecer de cada demanda em
> [`docs/demandas.md`](../../docs/demandas.md). Em conflito sobre "como", esta constituição vence o
> `CLAUDE.md` e qualquer spec/plano; em conflito sobre "o quê", vence a `VISAO.md`.
> Palavras-chave: **DEVE/NUNCA** = obrigatório; **DEVERIA** = padrão, desvio exige justificativa
> registrada no plano (tabela "Complexity Tracking").
>
> O Mark LV é um projeto **separado do Mark LVI**. Nada daqui depende do repositório do LVI, e
> nenhuma conversa ou tarefa do LV altera o LVI.

## Core Principles

### I. Evoluir a estrutura que existe, sem reescrever

- O Mark LV continua um app de desktop: um processo Python com PyQt6 (`ui.py`) e a sessão Gemini
  Live (`main.py`). Funcionalidade nova DEVE entrar pelos pontos de extensão que já existem:
  ação em `actions/` (mexe no computador), plugin em `plugins/` (integração pessoal ou serviço
  externo), módulo em `core/` (infraestrutura compartilhada), estilo de HUD (`HudCanvas`).
- Reescrita de arquitetura (núcleo separado, servidor, Docker) está **fora de escopo**: é o
  caminho do Mark LVI. Uma feature que só funcione com essa reescrita DEVE ser recusada aqui.
- Mudança em arquivo do upstream (`main.py`, `ui.py`, `actions/*`) DEVERIA ser pequena e
  localizada, para que `git merge main` (upstream) continue possível. Nada de reformatar ou
  reordenar código que a feature não precisa tocar.

**Por quê:** o valor do LV é estar funcionando hoje; e o `main` acompanha o FatihMakes.

### II. Spec antes de código; verificação antes de "pronto" (NÃO NEGOCIÁVEL)

- Toda feature (algo que um usuário perceberia) nasce de uma spec pelo Spec Kit
  (ver "Fluxo de desenvolvimento"). Correção pequena e localizada pode ir direto, com issue ou
  item no `CHANGELOG.md` explicando o porquê.
- Lógica sem interface (memória, política de arquivos, registro de erros, parsers, regras de
  agenda) DEVE ter teste `pytest` em `tests/`, escrito antes e visto falhando. Testes NUNCA chamam
  API real, rede, microfone ou câmera: Gemini, relógio e disco ficam atrás de funções que o teste
  substitui.
- O que é interface ou mídia DEVE ser verificado como o `CLAUDE.md` descreve: `py_compile` em tudo
  que mudou, HUD renderizado offscreen com imagem conferida e ms/quadro medido.
- Comportamento do modelo (escolher a ferramenta certa, falar antes de uma espera) não cabe em
  `assert`: DEVE ser conferido com roteiro de casos na spec e teste real com a chave, fora do
  sandbox, antes do merge.
- Nada é "pronto" com verificação pulada. Se algo não pôde ser verificado, o PR DEVE dizer o quê.

**Por quê:** o upstream não tem testes; a cada feature nova o risco de quebrar algo calado cresce.

### III. A thread Qt e o áudio nunca esperam

- Nada lento roda na thread Qt nem nos loops de áudio do `JarvisLive`. Trabalho lento vai para
  thread/executor e volta por `pyqtSignal`; o `main.py` só fala com a UI pela fachada `JarvisUI`.
- Ferramenta que pode passar de ~1 s DEVE se declarar `NON_BLOCKING` (com `scheduling`) ou avisar
  o usuário antes da espera. Silêncio depois de uma pergunta é falha, não detalhe.
- HUD: < 15 ms por quadro no tamanho real (r≈330 px), medido em GPU confirmada por `GL_RENDERER`.

### IV. Segurança proporcional ao poder

- Toda ação/plugin DEVE se classificar: **leitura**, **reversível** (entra no desfazer,
  `core/undo.py`) ou **irreversível** (enviar mensagem, apagar de vez, publicar, instalar,
  executar programa, gastar dinheiro). Irreversível DEVE passar pela confirmação real da interface
  (`core/confirm.py`), nunca por "sim" deduzido pelo modelo.
- Acesso a arquivos só dentro das pastas liberadas pelo usuário, conferido num ponto central;
  nenhuma ação lê ou escreve fora delas por um caminho próprio.
- NUNCA executar código escrito pelo modelo direto no sistema sem confirmação e sem mostrar o que
  vai rodar.
- Canais remotos (Telegram, painel) aceitam ordens só de identificadores na lista do usuário.

### V. Conteúdo externo é dado, nunca ordem

- Tudo que o JARVIS lê e o usuário não disse (páginas, e-mails, notas, arquivos, resultados de
  outros agentes) é não confiável. Instruções ali dentro NUNCA são seguidas, NUNCA dispensam a
  confirmação de uma ação irreversível e NUNCA viram memória de perfil sem confirmação.

### VI. Toda declaração custa tokens em toda conexão

- Cada ação/plugin ligado vai para o modelo em **toda** conexão. Descrição curta e precisa; nova
  ferramenta no cardápio DEVE justificar o custo na spec (caracteres somados à declaração).
- Integração grande é **uma** ferramenta com subcomandos, não dez ferramentas.

### VII. Falha explícita e registrada

- Erro de ação, plugin, modelo ou conexão DEVE virar mensagem clara ao usuário e registro
  persistente (`logs/`), com contexto e sem segredos. Nada de `except: pass` em caminho de usuário.
- Recurso opcional que falta (dependência, permissão de câmera, chave) DEVE dizer o que falta e
  como resolver; desligar em silêncio é proibido.

**Por quê:** abrir Excel falhava calado porque `pandas` era opcional; erros iam só para o console.

### VIII. Dados pessoais locais e segredos fora do git

- Memória, tokens OAuth, chaves e sessões ficam na máquina, em caminhos do `.gitignore`.
  Conferir `git status` e o diff antes de todo commit.
- Enquanto a chave Gemini estiver no nível gratuito, o Google pode usar o conteúdo para treino:
  integrações com dado sensível (e-mail, saúde, finanças) DEVEM avisar disso na spec e na
  interface até a chave paga estar ligada.
- O usuário DEVE conseguir ver e apagar o que o JARVIS guardou sobre ele.

### IX. Medido, não suposto; multiplataforma sempre

- Decisão de desempenho ou custo cita número medido, no código (comentário do porquê) e na spec.
- Nada que assuma Windows sem ramo para macOS/Linux; dependência só de um sistema leva marcador
  `sys_platform`. Dependência nova só com justificativa e importada no ponto de uso.

## Fluxo de desenvolvimento e portões de qualidade

- Branches: `main` espelha o upstream (FatihMakes) e nunca recebe commit nosso; `irlan-dev` é a
  base de desenvolvimento. Cada feature tem sua branch `NNN-nome` criada a partir de `irlan-dev`
  atualizada (o `/speckit-specify` cria) e volta por **pull request para `irlan-dev`**.
  Manutenção sem spec usa `fix/…`, `chore/…` ou `docs/…`.
- Ordem por feature: `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` →
  `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`. `/speckit-checklist` é opcional
  para features de risco (segurança, dados pessoais).
- O "Constitution Check" do plano confere cada princípio acima.
- Um PR só é mesclado com: verificação do Princípio II registrada no PR, `CHANGELOG.md`
  atualizado, nenhum segredo no diff, `graphify update .` rodado.
- Modelos: especificar, esclarecer e planejar ficam com o modelo mais forte da sessão; tarefas já
  planejadas em `tasks.md` podem ir para agentes mais baratos (ver `docs/HARNESS.md`). A revisão
  final de um PR nunca é feita só pelo agente que implementou.
- Idioma: conversa e documentos em pt-BR; código, identificadores, comentários e textos de UI em
  inglês (exceto onde o usuário pediu pt-BR, como o painel VOZ E JEITO).

## Governance

- Esta constituição vale acima de qualquer outra prática do projeto. Emenda só por pull request
  que altera este arquivo, explica o motivo e atualiza `CLAUDE.md`/`docs/VISAO.md` se afetados.
- Versão semântica: **MAJOR** remove ou redefine um princípio; **MINOR** acrescenta princípio ou
  seção, ou amplia regra de forma material; **PATCH** é redação/esclarecimento.
- Uma regra que ninguém consegue cumprir DEVE ser emendada, não ignorada.

**Version**: 1.0.0 | **Ratified**: 2026-10-16 | **Last Amended**: 2026-10-16
