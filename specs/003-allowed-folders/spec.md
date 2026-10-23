# Feature Specification: Pastas liberadas (política central de arquivos)

**Feature Branch**: `003-allowed-folders`

**Created**: 2026-10-22

**Status**: Draft

**Input**: User description: "Pastas liberadas — política central de arquivos. Um módulo central core/fs_policy.py com a lista de pastas liberadas pelo usuário e o modo de cada uma (só leitura / leitura e escrita), editável em ⚙ SETUP, e uma lista de negação fixa que nenhuma liberação vence (config/api_keys.json e demais segredos do app, .ssh, cofres de senha, .git interno). Todas as ações que tocam disco com caminho vindo do modelo passam por ele: file_controller, code_helper, file_processor, desktop, video_player, browser_control e computer_control, dev_agent. Recusa explica ao usuário qual pasta liberar. Testável por pytest sem interface. Fundação das specs de Obsidian (006) e auditoria de projetos (011). Ver docs/demandas.md §2 e docs/VISAO.md §5 item 3."

## Contexto

Hoje a segurança de arquivos do JARVIS é, ao mesmo tempo, **restritiva demais e frouxa demais**
([`docs/demandas.md` §2](../../docs/demandas.md)):

- **Restritiva demais:** a ação de arquivos (`file_controller`) só aceita caminhos dentro da pasta
  do usuário (`C:\Users\irlan`). Os projetos do Irlan em `C:\DESENVOLVIMENTO` ficam fora, e não há
  como liberar outra pasta.
- **Frouxa demais:** a regra vale só naquele arquivo. Levantamento feito em 2026-10-22 das ações
  que recebem um caminho escolhido pelo modelo:

| Ação | O que faz com o caminho | Conferência hoje |
|---|---|---|
| `file_controller` | lista, cria, lê, escreve, move, copia, renomeia, apaga (lixeira), procura | só "dentro da pasta do usuário" |
| `code_helper` | lê um arquivo, escreve/edita código, **executa** o arquivo | nenhuma (lê e grava qualquer caminho) |
| `file_processor` | lê o arquivo enviado, grava o resultado, extrai arquivo compactado em `destination` | nenhuma |
| `desktop` | lê a imagem do papel de parede; organiza/limpa a Área de Trabalho | nenhuma no `path` |
| `video_player` | abre um arquivo de vídeo local | nenhuma |
| `browser_control`, `computer_control` | gravam a captura de tela no `path` | nenhuma |
| `dev_agent` | grava os arquivos do projeto gerado dentro de `Desktop/JarvisProjects/<projeto>` | nome do arquivo vem do modelo e não é conferido (um `../` escapa da pasta) |

A constituição (Princípio IV) já exige: *"Acesso a arquivos só dentro das pastas liberadas pelo
usuário, conferido num ponto central; nenhuma ação lê ou escreve fora delas por um caminho
próprio."* Esta feature cumpre essa regra.

Ela é a **fundação** de três demandas: o plugin Obsidian (spec 006, escreve no cofre), a auditoria
de projetos (spec 011, lê repositórios em `C:\DESENVOLVIMENTO`) e a organização de arquivos.

Quem usa o resultado:
- **Irlan**, falando com o JARVIS: consegue pedir algo sobre os projetos em `C:\DESENVOLVIMENTO`
  (hoje recusado) e sabe que nada fora do que liberou será lido ou alterado.
- **Irlan**, em ⚙ SETUP: vê e edita, num só lugar, quais pastas o JARVIS alcança e com que poder.
- **Specs futuras (006, 011)**: perguntam à política "posso ler/escrever aqui?" em vez de cada uma
  inventar a sua regra.

## Clarifications

### Session 2026-10-22

- Q: Executar código escrito pelo modelo, além de exigir pasta em leitura e escrita, passa pela confirmação da interface? → A: Sim — confirmação real (`core/confirm.py`) mostrando o arquivo e o comando, já nesta spec.
- Q: O usuário pode liberar pasta por voz? → A: Não — só em ⚙ SETUP; a recusa diz onde liberar.
- Q: Qual o padrão na primeira execução depois da atualização? → A: A pasta do usuário em leitura e escrita (igual ao `file_controller` de hoje); nenhuma outra.
- Q: O desfazer de uma ação já feita obedece ao modo atual da pasta? → A: Não — o desfazer é sempre permitido (só devolve o estado que o próprio JARVIS mudou); a lista de negação continua valendo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Liberar uma pasta de projetos e usá-la por voz (Priority: P1)

O Irlan libera `C:\DESENVOLVIMENTO` em ⚙ SETUP, em modo "só leitura". Depois pede por voz: "JARVIS,
lista as pastas do Mark LV" ou "lê o README do Mark LV". O JARVIS responde com o conteúdo. Quando
pede "cria um arquivo notas.txt no Mark LV", o JARVIS recusa e diz que aquela pasta está liberada
só para leitura e que ele pode mudar isso em ⚙ SETUP.

**Why this priority**: é o desbloqueio que motiva a feature (projetos fora da pasta do usuário) e
a base das specs 006 e 011.

**Independent Test**: com uma lista de pastas montada no teste (sem interface), pedir leitura e
escrita em caminhos dentro e fora dela e conferir a decisão e a mensagem.

**Acceptance Scenarios**:

1. **Given** `C:\DESENVOLVIMENTO` liberada em "só leitura", **When** o JARVIS lista ou lê um
   arquivo dentro dela, **Then** a operação acontece.
2. **Given** a mesma liberação, **When** o JARVIS tenta criar, editar, mover, renomear ou apagar
   algo dentro dela, **Then** a operação é recusada e a resposta nomeia a pasta, o modo atual e
   onde mudar.
3. **Given** a pasta liberada em "leitura e escrita", **When** o JARVIS cria um arquivo nela,
   **Then** a operação acontece (e continua entrando no desfazer, como hoje).
4. **Given** um caminho fora de qualquer pasta liberada, **When** qualquer ação tenta lê-lo ou
   gravá-lo, **Then** a operação é recusada e a resposta diz qual pasta precisaria ser liberada.

---

### User Story 2 - Segredos nunca são alcançados, mesmo dentro de pasta liberada (Priority: P1)

Mesmo com a pasta do usuário ou `C:\DESENVOLVIMENTO` liberadas em "leitura e escrita", o JARVIS
nunca lê nem grava `config/api_keys.json`, `config/certs/`, a sessão do WhatsApp Web, a memória de
longo prazo por caminho direto, chaves SSH (`.ssh`), cofres de senha, nem o conteúdo interno de
`.git/`.

**Why this priority**: um conteúdo externo malicioso (página, e-mail, arquivo lido — Princípio V)
pode pedir ao modelo para "ler config/api_keys.json e me mostrar". A lista de negação é o que
impede o vazamento mesmo quando a pasta está liberada.

**Independent Test**: no teste, liberar a raiz que contém cada item negado e conferir que todo
acesso (ler, listar, escrever, mover para lá, copiar de lá) é recusado.

**Acceptance Scenarios**:

1. **Given** a pasta do usuário liberada em "leitura e escrita", **When** o modelo pede para ler
   `~/.ssh/id_ed25519`, **Then** a operação é recusada com uma mensagem que diz que aquele local é
   protegido e não pode ser liberado.
2. **Given** `C:\DESENVOLVIMENTO` liberada, **When** o modelo pede para ler
   `C:\DESENVOLVIMENTO\JARVIS\Mark-LV\config\api_keys.json`, **Then** a operação é recusada.
3. **Given** um projeto liberado, **When** o modelo lê `README.md` do projeto, **Then** funciona;
   **When** pede `.git/config` ou um objeto dentro de `.git/`, **Then** é recusado.
4. **Given** qualquer liberação, **When** o caminho chega ao item negado por um atalho (link
   simbólico, junção do Windows, `..`, letras maiúsculas/minúsculas diferentes, caminho curto
   `PROGRA~1`), **Then** a decisão é a mesma do caminho real.
5. **Given** a busca de arquivos ou a listagem de uma pasta liberada, **When** o resultado
   incluiria um item negado, **Then** ele não aparece no resultado.

---

### User Story 3 - Todas as ações que tocam disco obedecem à mesma regra (Priority: P1)

As ações da tabela do Contexto passam a perguntar à política central antes de ler, gravar ou
executar um caminho escolhido pelo modelo. Nenhuma tem regra própria.

**Why this priority**: sem isso a feature é só mais uma regra local; a falha de hoje é justamente
`code_helper` passar por fora.

**Independent Test**: para cada ação, chamar o ponto de entrada com um caminho fora das pastas
liberadas (disco simulado) e conferir a recusa sem nenhum efeito no disco.

**Acceptance Scenarios**:

1. **Given** um caminho fora das pastas liberadas, **When** `code_helper` é pedido para explicar,
   editar ou rodar esse arquivo, **Then** recusa sem ler o arquivo.
2. **Given** `code_helper` gravando código novo sem caminho, **When** o destino padrão (Área de
   Trabalho) está liberado para escrita, **Then** grava como hoje; **When** não está, **Then**
   recusa e explica.
3. **Given** `dev_agent` gerando um projeto, **When** o modelo devolve um nome de arquivo que sai
   da pasta do projeto (`../../x.py`, caminho absoluto), **Then** aquele arquivo é recusado e o
   resto do projeto segue.
4. **Given** `file_processor` extraindo um arquivo compactado, **When** `destination` está fora das
   pastas liberadas para escrita, **Then** recusa; **When** um item dentro do arquivo compactado
   tenta sair do destino (`../`), **Then** esse item não é extraído.
5. **Given** uma captura de tela pedida com `path`, **When** o caminho não está liberado para
   escrita, **Then** recusa.
6. **Given** um arquivo que o próprio Irlan arrastou ou enviou pela interface, **When**
   `file_processor` o lê, **Then** a leitura é permitida mesmo fora das pastas liberadas (o envio é
   o consentimento), mas só para aquele arquivo — e a lista de negação continua valendo.

---

### User Story 4 - Ver e editar as pastas liberadas em ⚙ SETUP (Priority: P2)

Em ⚙ SETUP há uma seção de pastas liberadas: a lista atual com o modo de cada uma, botões para
adicionar (escolhendo a pasta numa janela do sistema), mudar o modo e remover. A lista de negação
aparece ali só para leitura, para o Irlan saber o que nunca será alcançado. A mudança vale na hora,
sem reiniciar o app e sem reconectar a sessão de voz.

**Why this priority**: a política funciona sem a tela (lista no arquivo de configuração), mas o
Irlan não deveria editar JSON à mão para liberar uma pasta.

**Independent Test**: abrir ⚙ SETUP, adicionar uma pasta, mudar o modo, remover; conferir o arquivo
de configuração e uma decisão da política logo depois, sem reiniciar.

**Acceptance Scenarios**:

1. **Given** ⚙ SETUP aberto, **When** o Irlan adiciona `C:\DESENVOLVIMENTO` em "só leitura",
   **Then** a próxima operação de leitura nela já é permitida, sem reiniciar.
2. **Given** uma pasta que é ou contém um item negado (ex.: `~/.ssh`), **When** o Irlan tenta
   adicioná-la, **Then** a tela avisa que os itens protegidos continuam inacessíveis (ou recusa a
   própria pasta, se ela for o item negado).
3. **Given** uma pasta liberada que deixou de existir no disco, **When** ⚙ SETUP abre, **Then** ela
   aparece marcada como ausente e pode ser removida.

---

### Edge Cases

- **Pastas aninhadas com modos diferentes** (`C:\DESENVOLVIMENTO` só leitura, `…\Mark-LV\scratch`
  leitura e escrita): vale a regra da pasta liberada **mais específica** que contém o caminho.
- **Mover/copiar entre pastas**: a origem precisa de leitura (mover: escrita, pois some de lá) e o
  destino precisa de escrita. Qualquer uma das duas negada → nada acontece.
- **Caminho relativo** ("notas.txt"): resolvido como hoje (atalhos `desktop`, `documents`… e
  depois a pasta do usuário), e só então conferido. Nunca contra a pasta de trabalho do processo.
- **Arquivo que ainda não existe** (criar/gravar): confere a pasta onde ele vai nascer, já
  resolvida (links e `..` resolvidos no pai existente mais próximo).
- **Link simbólico ou junção dentro de pasta liberada apontando para fora**: a decisão segue o
  destino real; o link não abre uma porta.
- **Diferenças entre sistemas**: Windows compara caminhos sem diferenciar maiúsculas; macOS e
  Linux seguem o sistema de arquivos. Unidades de rede e caminhos UNC só passam se a pasta liberada
  for exatamente aquele local.
- **Arquivo de configuração das pastas ausente ou corrompido**: o app abre com o padrão de primeira
  execução (ver Assumptions), registra a falha em `logs/` e avisa em ⚙ SETUP; nunca libera "tudo"
  por falta de configuração.
- **Varredura grande** (busca de arquivos, organizar Área de Trabalho, listar pasta com milhares de
  itens): a conferência por item não pode deixar a ação perceptivelmente mais lenta.
- **Desfazer** (`core/undo.py`) de algo feito numa pasta que depois virou "só leitura" ou saiu da
  lista: é **sempre permitido**, porque só devolve o estado anterior que o próprio JARVIS mudou; a
  lista de negação continua valendo.

## Requirements *(mandatory)*

### Functional Requirements

**Política central**

- **FR-001**: O app DEVE manter uma única lista de **pastas liberadas**, cada uma com um modo:
  **só leitura** ou **leitura e escrita**. Ela DEVE ficar salva na configuração local do app e
  sobreviver a reinícios.
- **FR-002**: DEVE existir um único ponto de decisão que responde, para um caminho e uma operação
  (**ler**, **escrever**, **executar**), se a operação é permitida e, quando não é, o motivo.
  Nenhuma ação decide isso por conta própria.
- **FR-003**: "Escrever" inclui criar, gravar, editar, renomear, mover (na origem e no destino),
  copiar (no destino), apagar (mesmo indo para a lixeira) e extrair. "Ler" inclui ler conteúdo,
  listar, procurar e obter informações de um item.
- **FR-004**: Antes de decidir, o caminho DEVE ser normalizado até o local real: atalhos de pasta
  resolvidos, `..` e `.` resolvidos, links simbólicos e junções seguidos, forma curta do Windows
  expandida, comparação sem diferenciar maiúsculas no Windows.
- **FR-005**: Quando várias pastas liberadas contêm o caminho, vale o modo da **mais específica**.
- **FR-006**: Executar um arquivo (`code_helper` rodar, `dev_agent` rodar o projeto, a tarefa
  livre do `desktop` que roda código escrito pelo modelo) DEVE exigir que o arquivo esteja em pasta
  liberada em **leitura e escrita** **e** passar pela confirmação real da interface
  (`core/confirm.py`), que mostra o arquivo e o comando que vai rodar (Princípio IV). Recusa na
  confirmação ou confirmação sem resposta → nada roda, e o modelo recebe que o usuário não
  autorizou.

**Lista de negação fixa**

- **FR-007**: DEVE existir uma **lista de negação** que nenhuma liberação vence, para ler,
  escrever e executar. No mínimo:
  - os segredos do próprio app: `config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`,
    `memory/long_term.json` (acessada só pela memória do app, nunca por caminho);
  - chaves e credenciais do usuário: `~/.ssh/`, `~/.gnupg/`, `~/.aws/`, `~/.azure/`, `~/.kube/`,
    `~/.docker/config.json`, `~/.git-credentials`, `~/.netrc`, `~/.pypirc`, `~/.npmrc`;
  - cofres de senha por extensão em qualquer lugar: `*.kdbx`, `*.kdb`, `*.1pux`, `*.opvault`, e
    as pastas de dados dos gerenciadores de senha e dos perfis de navegador (senhas e cookies
    salvos);
  - o conteúdo interno de qualquer repositório git: tudo dentro de uma pasta `.git/` (o
    `.gitignore` e os arquivos do projeto continuam acessíveis).
- **FR-008**: A lista de negação NÃO DEVE ser editável pela interface nem pela voz; ela é mostrada
  só para leitura em ⚙ SETUP.
- **FR-009**: Listagens e buscas em pastas liberadas DEVEM omitir os itens negados do resultado (e
  não descer dentro de pastas negadas).

**Ações que tocam disco**

- **FR-010**: Toda ação ou plugin que lê, grava ou executa um caminho escolhido pelo modelo DEVE
  consultar o ponto de decisão antes de tocar o disco. Nesta feature, no mínimo: `file_controller`,
  `code_helper`, `file_processor`, `desktop`, `video_player`, `browser_control`,
  `computer_control` e `dev_agent`.
- **FR-011**: Um arquivo que o usuário enviou ou arrastou pela interface DEVE poder ser lido pelo
  `file_processor` mesmo fora das pastas liberadas, só aquele arquivo e só enquanto ele for o
  arquivo enviado atual; a lista de negação continua valendo. Os resultados gravados a partir dele
  seguem a regra normal de escrita.
- **FR-012**: Nomes de arquivo produzidos pelo modelo dentro de uma pasta de trabalho
  (`dev_agent`, itens de um arquivo compactado em `file_processor`) DEVEM ficar dentro dela; um
  item que escape é recusado sem interromper os demais.
- **FR-013**: Arquivos internos que o app grava por caminho fixo (logs, cache de voz, cache de
  avatar, configuração) NÃO passam pela política: ela governa caminhos escolhidos pelo modelo, não
  a infraestrutura do app.
- **FR-014**: Plugins em `plugins/` DEVEM ter acesso ao mesmo ponto de decisão, e o modelo de
  plugin (`plugins/_template.py`) DEVE mostrar como usá-lo.

**Recusa e registro**

- **FR-015**: Uma recusa DEVE virar uma resposta clara para o modelo repassar ao usuário, dizendo:
  o caminho (ou a pasta mais próxima), o motivo (fora das pastas liberadas / pasta só leitura /
  local protegido) e o que fazer (liberar em ⚙ SETUP, com o nome da pasta sugerida), sem expor o
  conteúdo de nenhum arquivo.
- **FR-016**: Toda recusa DEVE ser registrada em `logs/` (ação, operação, caminho normalizado,
  motivo), passando pelo filtro de segredos, num tipo próprio de registro ("recusa por política"),
  separado das falhas do app e sem disparar o relato automático (ver research R4).

**Configuração na interface**

- **FR-017**: ⚙ SETUP DEVE ter uma seção para ver, adicionar (com seletor de pasta do sistema),
  mudar o modo e remover pastas liberadas, e mostrar a lista de negação só para leitura.
- **FR-018**: Mudanças DEVEM valer na próxima operação, sem reiniciar o app e sem reconectar a
  sessão de voz.
- **FR-019**: Pastas só são liberadas, mudadas ou removidas em ⚙ SETUP (ou no arquivo de
  configuração, à mão). Nenhuma ação do modelo altera a lista; um pedido por voz recebe a
  explicação de onde liberar. Assim um conteúdo externo malicioso nunca leva a uma liberação.

**Restrições**

- **FR-020**: A decisão DEVE ser testável por `pytest` sem interface, sem rede e com disco
  simulado (pastas temporárias), cobrindo os cenários das US1–US3.
- **FR-021**: Nenhuma ferramenta nova é declarada ao modelo, e as descrições das ferramentas
  existentes não crescem mais que o necessário para dizer que o acesso se limita às pastas
  liberadas (Princípio VI; o plano mede os caracteres somados).
- **FR-022**: Funciona em Windows, macOS e Linux (caminhos da lista de negação com equivalentes
  por sistema).

### Key Entities

- **Pasta liberada**: um local do disco (caminho absoluto normalizado), um modo (só leitura /
  leitura e escrita) e, opcionalmente, um rótulo para a interface. Muitas por usuário.
- **Item negado**: um padrão fixo (caminho exato, pasta, nome em qualquer nível ou extensão), com
  o motivo mostrado ao usuário ("chaves do app", "chaves SSH", "cofre de senhas", "interno do git").
- **Decisão**: para (caminho, operação, ação que pediu) → permitido ou recusado, com o motivo e a
  pasta sugerida para liberar.
- **Arquivo enviado**: o arquivo atual que o usuário arrastou ou enviou; dá leitura só a ele.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Depois de liberar `C:\DESENVOLVIMENTO`, pedidos de leitura por voz sobre os projetos
  ali funcionam (hoje 100% recusados).
- **SC-002**: 0 caminhos lidos, gravados ou executados por ações com caminho do modelo sem passar
  pelo ponto de decisão — conferido por teste para cada ação da tabela do Contexto.
- **SC-003**: 0 acessos aos itens da lista de negação nos testes, inclusive por link simbólico,
  junção, `..`, maiúsculas e forma curta, com a raiz que os contém liberada em leitura e escrita.
- **SC-004**: 100% das recusas no roteiro de teste real dizem ao usuário o motivo e onde liberar.
- **SC-005**: A conferência não deixa nenhuma ação perceptivelmente mais lenta: busca em uma pasta
  com 10 mil arquivos leva no máximo 10% a mais que hoje (medido no plano).
- **SC-006**: Uma pasta adicionada ou mudada em ⚙ SETUP vale na operação seguinte, sem reiniciar.
- **SC-007**: O que hoje funciona dentro da pasta do usuário continua funcionando no primeiro uso
  depois da atualização (nenhuma regressão percebida).

## Roteiro de teste real (Princípio II)

Um único roteiro, com a chave real e o app rodando, depois dos testes `pytest`:

1. Em ⚙ SETUP, liberar `C:\DESENVOLVIMENTO` em só leitura. Pedir: "lista as pastas do Mark LV" →
   lista. "Lê o README do Mark LV" → lê.
2. "Cria um arquivo teste.txt no Mark LV" → recusa nomeando a pasta, o modo e ⚙ SETUP.
3. "Lê o arquivo config/api_keys.json do Mark LV" → recusa como local protegido, sem conteúdo.
4. "Explica o código de C:\Windows\win.ini" (via `code_helper`) → recusa por estar fora.
4b. "Escreve e roda um script Python que imprime olá na Área de Trabalho" → a janela de
   confirmação mostra o arquivo e o comando; recusar → nada roda; aceitar → roda.
5. "Cria um arquivo notas.txt na Área de Trabalho" → funciona e o desfazer o remove (regressão).
6. Arrastar um PDF de uma pasta não liberada e pedir um resumo → funciona (arquivo enviado).
7. Mudar `C:\DESENVOLVIMENTO` para leitura e escrita em ⚙ SETUP sem reiniciar e repetir o passo 2
   → funciona; apagar o arquivo criado.

## Assumptions

- **Padrão de primeira execução** (para não quebrar o que funciona hoje — SC-007): a pasta do
  usuário entra liberada em **leitura e escrita**, como é hoje no `file_controller`. Nenhuma outra
  pasta vem liberada; `C:\DESENVOLVIMENTO` o Irlan libera em ⚙ SETUP (decidido em 2026-10-22). O plano registra se a
  proteção de pastas críticas que já existe no `file_controller` (Windows, Program Files…)
  continua lá ou passa para a política.
- O desfazer (`core/undo.py`), a lixeira e a proteção de pastas críticas já existentes continuam;
  esta feature acrescenta a conferência, não troca esses mecanismos.
- A regra vale para caminhos escolhidos pelo modelo. Abrir um programa (`open_app`), tocar mídia
  por URL e o que o app grava em `logs/`/`config/` por conta própria ficam fora.
- A política não tenta impedir um programa que o próprio usuário mandou executar de ler o disco
  depois de iniciado; ela governa o que as ações do JARVIS tocam diretamente.
- Fora de escopo: permissões por ação (ex.: "só o Obsidian escreve no cofre"), cota de espaço,
  liberação temporária com prazo. As specs 006 e 011 podem pedir isso depois.
