# Feature Specification: Registro de falhas e medição de uso de LLM

**Feature Branch**: `001-error-log-llm-usage`

**Created**: 2026-10-16

**Status**: Draft

**Input**: User description: "Registro de falhas e medição de uso de LLM (feature 001 do roteiro do Mark LV): registro persistente de falhas em `logs/` (arquivo rotativo + JSON Lines de exceções de ações/plugins, falhas da escada do Gemini e erros de conexão da sessão Live, com contexto, versão e filtro de segredos, agrupadas por assinatura); medição de uso de LLM (provedor, modelo, tokens, latência, erros de cota, custo por tabela de preços); ferramenta `report_problem` para quando o Irlan diz 'não funcionou'; base de `pytest` para a lógica pura; mudanças mínimas nos arquivos do upstream."

## Contexto

Hoje os erros do JARVIS vão para o console e para o log da tela e somem quando o app fecha
([`docs/demandas.md` §14](../../docs/demandas.md)). Também não se sabe quantos tokens a voz e as
chamadas avulsas gastam, quantas vezes por dia a cota grátis acaba, nem quanto custaria pagar.
Sem esses dados não dá para corrigir falhas (constituição, Princípio VII), medir latência (spec
002) nem decidir a gestão de modelos (D10) e o teto do modo privado (D12)
([`docs/llm-gestao.md` §6](../../docs/llm-gestao.md)).

Quem usa o resultado:
- **Irlan**, falando com o JARVIS (relata "não funcionou") e consultando um resumo de uso.
- **O Claude Code**, pela skill `lv-triage`, que lê os registros para propor correções.

## Clarifications

### Session 2026-10-16

- Q: O relato do "não funcionou" guarda a transcrição das últimas falas? → A: Sim, só no relato: as últimas ~3 trocas de fala (cortadas e filtradas); falhas automáticas nunca guardam conversa.
- Q: Quando a escada do Gemini esgota, o que o usuário vê na hora? → A: Uma linha curta no log da tela com o motivo (cota/fora do ar), no máximo uma vez a cada poucos minutos; o retorno das ações e a voz não mudam.
- Q: Por quanto tempo guardar os registros de uso de LLM? → A: 12 meses, um arquivo por mês; os mais antigos são apagados automaticamente.
- Q: Onde o Irlan consulta o resumo de uso? → A: Só por comando no terminal (script em `tools/`), sem ferramenta de voz nem painel; cartões no HUD ficam para a spec 010.
- Q: O JARVIS oferece registrar um relato sozinho quando uma ferramenta falha? → A: Não; só registra quando o Irlan pede ("não funcionou", "anota esse problema"). Sem regra nova no prompt.

### Session 2026-10-17

- Q: Uma exceção que escapa de um slot do Qt deve fechar o app (comportamento do PyQt6 com o hook padrão) ou ser registrada e o app seguir? → A: Registrar e seguir rodando (decisão do Irlan durante a implementação; consequência do `sys.excepthook` próprio).

### Session 2026-10-18

- Q: Duas rodadas de voz real mostraram que o modelo anota o "não funcionou" só em parte das vezes (2 de 4 na 2ª rodada): quando a frase traz "eu queria X", ele faz X e esquece de anotar. Como garantir o relato? → A: Detectar no código, sem depender do modelo: quando a fala do Irlan no turno contém "não funcionou", "não deu certo" ou "deu errado", o JARVIS grava o relato sozinho (a frase dele como expectativa, a transcrição e as últimas ferramentas) e mostra uma linha curta no log da tela. A ferramenta `report_problem` continua para "anota esse problema" e para quando o modelo pergunta e completa a expectativa; relato automático e da ferramenta no mesmo turno viram um só. Sem regra nova no prompt (decisão do Irlan).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Falhas ficam guardadas, agrupadas e sem segredos (Priority: P1)

Uma ação, um plugin, uma chamada ao Gemini ou a conexão de voz falha durante o uso. O app
continua funcionando como hoje, mas a falha fica registrada em disco com o que estava sendo
feito, a versão do JARVIS e o erro — sem chaves nem tokens. Depois de uma sessão de uso, o Irlan
(ou a `lv-triage`) abre os registros e vê as falhas agrupadas: "esta falha aconteceu 7 vezes,
primeira às 10h02, última às 15h40, na versão X".

**Why this priority**: é a base de todo o resto (Princípio VII). Sem isso, nenhuma correção parte
de dado real e as falhas "caladas" continuam escondidas.

**Independent Test**: provocar de propósito uma exceção numa ação e num plugin de teste, um 429
simulado na escada e uma queda da conexão de voz; fechar o app; conferir que cada falha está no
registro, com contexto, versão e assinatura, que repetições caem no mesmo grupo e que uma chave
plantada no contexto aparece mascarada.

**Acceptance Scenarios**:

1. **Given** o JARVIS em uso, **When** uma ação embutida lança uma exceção, **Then** o registro
   ganha uma entrada com a origem (nome da ação), os argumentos recebidos (filtrados e cortados),
   o tipo e a mensagem do erro, o traceback, a versão e a assinatura — e o usuário continua vendo
   a mensagem de erro que já vê hoje.
2. **Given** um plugin ligado, **When** ele falha ao carregar ou ao executar, **Then** a falha é
   registrada do mesmo jeito, com o nome do plugin como origem.
3. **Given** uma chamada avulsa ao Gemini, **When** um degrau da escada responde com falta de cota,
   indisponibilidade ou estoura o tempo, **Then** cada degrau que falhou é registrado com o motivo
   classificado (cota / indisponível / modelo inexistente / tempo esgotado / outro); **and when**
   todos os degraus falham, **Then** fica registrado um evento "escada esgotada" com a lista de
   degraus tentados **and** aparece uma linha curta no log da tela com o motivo (ex.: cota grátis
   esgotada em 8 modelos e quando ela volta), sem repetir a cada chamada.
4. **Given** a sessão de voz conectada, **When** a conexão cai, é recusada ou reconecta por erro,
   **Then** o evento é registrado com o modelo de voz, o motivo e se houve retomada da sessão.
5. **Given** a mesma falha acontecendo várias vezes, **When** os registros são consultados,
   **Then** todas as ocorrências têm a mesma assinatura e aparecem como um único grupo com
   contagem, primeira e última ocorrência.
6. **Given** um erro cuja mensagem ou contexto contém uma chave de API, um token de bot ou um
   cabeçalho de autorização, **When** ele é registrado, **Then** o segredo aparece mascarado e
   nunca por inteiro em nenhum arquivo de `logs/`.

---

### User Story 2 - Saber quanto LLM o JARVIS usa e quanto custaria (Priority: P2)

Cada uso de modelo — a conversa por voz e cada chamada avulsa (screenshot, código, documento,
pesquisa, amostra de voz) — gera um registro de uso: provedor, modelo, tokens de entrada e saída
(separando áudio de texto quando o provedor informa), latência, se deu erro de cota e o custo
calculado por uma tabela de preços. No nível grátis o custo real é zero, mas o registro guarda
também **quanto custaria no pago**. Depois de duas semanas, o Irlan pede um resumo e vê, por dia e
por modelo: chamadas, tokens, minutos de voz, vezes que a cota acabou (e em que horário) e o custo
estimado.

**Why this priority**: é o dado que decide D10 (tetos e roteador, spec 004) e D12 (se o modo
privado pago compensa). Vem depois de P1 porque usa a mesma base de registro.

**Independent Test**: com o provedor substituído por um falso que devolve contagens de tokens
conhecidas, fazer chamadas avulsas e turnos de voz simulados e conferir os registros e o resumo
diário (tokens somados, custo batendo com a tabela, cota esgotada contada no horário certo).
Depois, uma sessão real curta com a chave confirma que os números do provedor chegam.

**Acceptance Scenarios**:

1. **Given** uma chamada avulsa que responde, **When** ela termina, **Then** existe um registro de
   uso com provedor, modelo que respondeu, tier pedido (FAST/SMART/SEARCH ou modelo explícito),
   tokens de entrada/saída, latência e custo estimado.
2. **Given** a conversa por voz, **When** o modelo termina um turno e informa o consumo, **Then** o
   uso daquele turno é registrado, separando tokens de áudio e de texto quando informados, e a
   duração da sessão de voz fica conhecida ao fim dela.
3. **Given** um modelo que não está na tabela de preços, **When** o uso dele é registrado, **Then**
   os tokens são guardados, o custo fica marcado como "desconhecido" (não zero) e nada quebra.
4. **Given** um dia de uso registrado, **When** o Irlan roda o comando de resumo no terminal, **Then** ele vê por
   dia, provedor e modelo: número de chamadas, tokens, minutos de voz, quantidade e horários de
   falta de cota, e custo estimado no pago — total e por modelo.
5. **Given** a tabela de preços com a data em que foi conferida, **When** os preços mudarem,
   **Then** basta editar a tabela (sem mexer em código) e os próximos custos usam o valor novo;
   registros antigos guardam o custo calculado na hora.

---

### User Story 3 - "Não funcionou": o JARVIS anota o que eu esperava (Priority: P3)

O Irlan pede algo, o JARVIS faz errado ou não faz, e ele diz "não funcionou" (ou "anota esse
problema"). O JARVIS registra um relato com o que o Irlan esperava, o que aconteceu na visão dele,
e as últimas ferramentas chamadas e falhas da sessão, confirma em voz com uma frase curta e segue
a conversa. Na triagem, esses relatos aparecem primeiro.

**Why this priority**: capta os problemas que não geram exceção (ferramenta errada, resposta
errada, silêncio) — justamente os que o registro automático não vê. Depende da base de P1.

**Independent Test**: chamar a ferramenta `report_problem` diretamente com um texto de expectativa
e um histórico simulado de ferramentas; conferir o relato gravado (com expectativa, contexto e
versão, sem segredos). Depois, numa sessão real, dizer "não funcionou, eu queria X" e conferir que
o modelo escolhe a ferramenta e confirma em voz.

**Acceptance Scenarios**:

1. **Given** uma conversa em andamento, **When** o Irlan diz que algo não funcionou e o que
   esperava, **Then** o JARVIS chama `report_problem`, um relato é gravado com a expectativa nas
   palavras dele, a descrição do que aconteceu, as últimas ~3 trocas de fala (transcrição cortada
   e filtrada), as últimas ferramentas chamadas (nome e resultado resumido) e as falhas recentes
   da sessão, e o JARVIS confirma com uma frase curta.
2. **Given** o Irlan diz só "não funcionou", sem dizer o que esperava, **When** o JARVIS vai
   registrar, **Then** ele pergunta em uma frase o que era esperado antes de gravar — ou grava com
   o que tiver se o Irlan não quiser detalhar.
3. **Given** a declaração da ferramenta, **When** o JARVIS conecta, **Then** ela soma no máximo
   ~300 caracteres ao cardápio de ferramentas (Princípio VI).

---

### Edge Cases

- **Disco cheio, pasta `logs/` sem permissão ou arquivo travado por outro processo:** o registro
  falha em silêncio para o usuário, mas o app nunca cai nem trava por isso; um aviso único vai
  para o console e para o log da tela.
- **O próprio registro lança exceção** (dado não serializável, por exemplo): o dado problemático é
  convertido em texto ou descartado; registrar nunca derruba quem chamou.
- **Volume:** uma sessão de voz longa gera centenas de turnos; os arquivos têm limite de tamanho e
  rotação, e o espaço total de `logs/` fica limitado.
- **Rajada de falhas iguais** (um plugin falhando em loop): todas contam no grupo, mas o arquivo
  não cresce sem limite nem o console é inundado.
- **Sem git** (JARVIS instalado de um zip): a versão registrada cai para a versão do `CHANGELOG.md`
  ou "desconhecida", sem erro.
- **Segredo dentro de argumento de ferramenta ou de resultado** (o modelo repassou uma chave que o
  usuário ditou): mascarado como qualquer outro segredo.
- **Uso sem contagem de tokens** (o provedor não informou, ou a chamada falhou no meio): o registro
  existe mesmo assim, com tokens "desconhecidos", latência e resultado.
- **Chamada avulsa feita por sessão de voz descartável** (o primeiro degrau da escada): o uso é
  atribuído ao modelo de voz usado, como chamada avulsa — não como conversa.
- **Mudança de dia no meio da sessão:** o resumo diário divide pelo horário de cada registro.
- **Fuso:** a cota grátis do Gemini zera à meia-noite do Pacífico (4–5h em Brasília); o resumo
  mostra horários no fuso local e indica o dia de cota do provedor.
- **Registro corrompido** (linha cortada por queda de energia): quem lê ignora a linha ruim e
  segue com as outras.

## Requirements *(mandatory)*

### Functional Requirements

**Registro de falhas**

- **FR-001**: O sistema DEVE manter um registro de texto rotativo em `logs/` com o que hoje só vai
  para o console (avisos e erros do app), com limite de tamanho por arquivo e número de cópias.
- **FR-002**: O sistema DEVE manter um registro estruturado de falhas em `logs/`, uma falha por
  linha, legível por máquina, cada uma com: horário, origem (componente e nome — ação, plugin,
  escada do Gemini, sessão de voz, relato), tipo e mensagem do erro, traceback quando houver,
  contexto (o que estava sendo feito), versão do JARVIS e assinatura.
- **FR-003**: Exceções de ações embutidas e de plugins (ao carregar e ao executar) DEVEM ser
  registradas, sem mudar o que o usuário vê hoje nem o isolamento que impede o app de cair.
- **FR-004**: Cada degrau da escada do Gemini que falhar DEVE ser registrado com o motivo
  classificado (cota, indisponível, modelo inexistente, tempo esgotado, outro) e o tier pedido;
  quando nenhum degrau responder, DEVE ser registrado um evento de escada esgotada com os degraus
  tentados.
- **FR-004a**: Quando a escada esgotar, o usuário DEVE ver uma linha curta no log da tela dizendo o
  motivo predominante (cota grátis esgotada, modelos fora do ar ou ambos) e, se for cota, o horário
  local em que ela volta; a mesma linha NÃO se repete por alguns minutos (limite definido no
  plano). O texto devolvido pelas ações e a fala do JARVIS não mudam nesta spec.
- **FR-005**: Falhas da sessão de voz (conexão recusada, queda, reconexão por erro, modelo de voz
  indisponível, falha ao retomar a sessão) DEVEM ser registradas com o modelo de voz, o motivo e o
  número de tentativas de reconexão.
- **FR-006**: Toda falha DEVE ter uma assinatura estável que identifica "a mesma falha": origem +
  tipo do erro + o ponto do código do projeto onde ela nasceu (arquivo e função), sem depender da
  mensagem variável nem do número da linha. Falhas iguais DEVEM ser agrupáveis com contagem,
  primeira e última ocorrência.
- **FR-007**: Antes de qualquer gravação em `logs/`, o conteúdo DEVE passar por um filtro de
  segredos que mascara pelo menos: as chaves configuradas no app (pelo valor literal), chaves de
  API em formatos conhecidos (Google, OpenAI, DeepSeek, Anthropic), tokens de bot do Telegram,
  cabeçalhos de autorização/bearer e campos com nomes como `key`, `token`, `secret`, `password`.
- **FR-008**: Texto de contexto, argumentos e resultados registrados DEVEM ser cortados num
  tamanho máximo, para que um documento ou imagem passado a uma ferramenta não vá parar no log.
- **FR-009**: Registrar NUNCA pode derrubar, travar nem atrasar perceptivelmente quem chamou: a
  thread da interface e os loops de áudio não esperam gravação em disco (Princípio III); qualquer
  falha do próprio registro é contida.
- **FR-010**: O espaço ocupado por `logs/` DEVE ser limitado: o log de texto e o registro de falhas
  rodam por tamanho com descarte dos arquivos mais antigos; os registros de uso ficam em um arquivo
  por mês e os meses com mais de 12 meses são apagados automaticamente.

**Medição de uso de LLM**

- **FR-011**: Toda chamada avulsa a modelo (as que passam pela escada do Gemini e as amostras de
  voz) DEVE gerar um registro de uso com: horário, provedor, modelo que respondeu, tipo de uso
  (chamada avulsa, conversa por voz, amostra de voz), tier pedido, tokens de entrada e de saída,
  latência, resultado (ok, cota, indisponível, tempo, erro) e custo estimado.
- **FR-012**: A conversa por voz DEVE registrar o consumo informado pelo provedor a cada turno,
  separando tokens de áudio e de texto quando informados, além do início, fim e duração de cada
  sessão de voz.
- **FR-013**: O custo DEVE ser calculado por uma tabela de preços editável fora do código (por
  provedor e modelo, preço por milhão de tokens de entrada e saída, separado por modalidade quando
  o preço difere), com a data em que foi conferida. Valores iniciais: os de
  [`docs/llm-gestao.md` §2](../../docs/llm-gestao.md).
- **FR-014**: Cada registro de uso DEVE dizer se a chamada foi no nível grátis ou pago e guardar o
  custo **estimado no pago** mesmo quando o custo real é zero.
- **FR-015**: Modelo sem preço na tabela, ou uso sem contagem de tokens, DEVE gerar custo
  "desconhecido" (distinto de zero), sem erro.
- **FR-016**: DEVE existir um resumo de uso que, a partir dos registros, mostra por dia, provedor e
  modelo: chamadas, tokens, minutos de voz, quantidade e horários de falta de cota, e custo
  estimado — total e por modelo — obtido sem chamar nenhum serviço externo. O resumo é um comando
  de terminal (ferramenta de desenvolvimento em `tools/`, com período escolhível, ex.: últimos 14
  dias); o JARVIS não ganha ferramenta de voz nem painel para isso nesta spec.

**Relato do usuário**

- **FR-017**: O JARVIS DEVE ter uma ferramenta `report_problem` que grava um relato com: o que o
  usuário esperava (nas palavras dele), o que aconteceu, a transcrição das últimas ~3 trocas de
  fala (usuário e JARVIS, cortada e filtrada), as últimas ferramentas chamadas na sessão (nome e
  resultado resumido) e as falhas recentes da sessão, mais horário e versão. Falhas automáticas
  NUNCA guardam transcrição de conversa.
- **FR-018**: A declaração de `report_problem` DEVE somar no máximo ~300 caracteres ao cardápio do
  modelo, e a spec do plano DEVE registrar o tamanho medido (Princípio VI).
- **FR-019**: Relatos DEVEM ser distinguíveis das falhas automáticas no registro, para que a
  triagem os trate primeiro.
- **FR-019a**: `report_problem` é chamado só quando o usuário pede ("não funcionou", "anota esse
  problema" e equivalentes); o JARVIS NÃO oferece registrar por conta própria e o prompt do
  sistema não ganha regra nova para isso — a própria declaração da ferramenta basta.
- **FR-019b**: Além da ferramenta, o JARVIS DEVE gravar um relato **automático** (sem depender do
  modelo) quando a fala do usuário num turno contém "não funcionou", "não deu certo" ou "deu
  errado" (com e sem acento; também "didn't work"/"did not work"), com a frase do usuário como
  expectativa, a transcrição e as últimas ferramentas, e mostrar uma linha curta no log da tela
  confirmando. Se a ferramenta `report_problem` já registrou no mesmo turno, o automático não
  duplica. Todo relato diz a origem (`tool` ou `auto`). Decidido em 2026-10-18 (Clarifications).

**Testes e escopo**

- **FR-020**: A lógica sem interface — filtro de segredos, cálculo de assinatura, agrupamento,
  cálculo de custo e resumo de uso — DEVE ter testes automatizados em `tests/`, escritos antes e
  vistos falhando, sem rede, sem chave real, sem microfone (Princípio II).
- **FR-021**: As mudanças em arquivos do upstream (`main.py`, `ui.py`, `actions/*`) DEVEM ser
  pequenas e localizadas (ganchos de uma ou poucas linhas chamando um módulo novo), sem reformatar
  nem reordenar código que a feature não precisa tocar (Princípio I).
- **FR-022**: Os formatos dos registros DEVEM ser documentados de forma que a skill `lv-triage` os
  leia sem adivinhar.

### Key Entities

- **Falha (Failure)**: um erro que aconteceu. Horário, origem (componente + nome), tipo, mensagem,
  traceback, contexto, versão, assinatura, sessão de voz em que ocorreu (se houver).
- **Grupo de falhas**: falhas com a mesma assinatura. Contagem, primeira e última ocorrência,
  versões em que apareceu, um exemplo.
- **Relato (Problem report)**: o "não funcionou" do usuário. Expectativa, o que aconteceu, últimas
  ~3 trocas de fala, últimas ferramentas, falhas recentes, horário, versão.
- **Registro de uso (Usage record)**: um uso de modelo. Horário, provedor, modelo, tipo de uso,
  tier, tokens por modalidade, latência, resultado, nível (grátis/pago), custo estimado.
- **Sessão de voz**: início, fim, modelo, voz, reconexões; agrega os registros de uso dos turnos.
- **Tabela de preços**: por provedor e modelo, preço por milhão de tokens de entrada/saída por
  modalidade, data da conferência, fonte.
- **Versão**: identificação do código em execução (commit curto e versão do `CHANGELOG.md`).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Numa sessão de teste com falhas provocadas (ação, plugin, cota na escada, escada
  esgotada, queda da voz), 100% delas aparecem no registro depois de fechar o app, cada uma com
  origem, contexto, versão e assinatura.
- **SC-002**: Repetir a mesma falha 5 vezes produz 1 grupo com contagem 5; duas falhas diferentes
  na mesma ação produzem 2 grupos.
- **SC-003**: Com chaves plantadas no contexto, na mensagem e nos argumentos, 0 ocorrências do
  segredo inteiro em qualquer arquivo de `logs/`.
- **SC-004**: Numa sessão real de 10 minutos de voz com algumas chamadas avulsas, pelo menos 95%
  dos usos de modelo têm registro com contagem de tokens, e o custo estimado do resumo bate com a
  conta manual pela tabela (diferença < 1%).
- **SC-005**: O resumo de uso de duas semanas de registros sai em menos de 5 segundos.
- **SC-006**: Registrar não acrescenta atraso perceptível: a parte feita na thread de quem chama
  leva menos de 1 ms por registro (medido), e o HUD segue abaixo de 15 ms/quadro.
- **SC-007**: Dizer "não funcionou, eu queria X" numa sessão real resulta em um relato gravado com
  X em pelo menos 4 de 5 tentativas, com confirmação (em voz pela ferramenta ou pela linha no log
  da tela do relato automático, FR-019b).
- **SC-008**: O log de texto e o registro de falhas não passam do limite de tamanho definido mesmo
  com um plugin falhando em loop por 10 minutos; registros de uso com mais de 12 meses não existem
  em `logs/`.
- **SC-009**: A suíte de testes da lógica pura roda em menos de 10 segundos sem rede.

## Assumptions

- **Local e privado:** tudo fica em `logs/`, já no `.gitignore`; nada é enviado para fora. Os
  registros são arquivos de texto comuns: o Irlan vê e apaga o que foi guardado abrindo ou apagando
  os arquivos de `logs/` (constituição, Princípio VIII); o `CLAUDE.md` documenta isso. A
  triagem e a abertura de issues continuam manuais pela `lv-triage`, com confirmação do Irlan.
- **Sem conteúdo de conversa nos registros:** o registro guarda nomes de ferramentas, argumentos
  filtrados e cortados e mensagens de erro — não a transcrição da conversa. A exceção é o relato,
  que guarda as palavras do Irlan sobre o que esperava e as últimas ~3 trocas de fala (decidido no
  clarify de 2026-10-16).
- **Provedores:** hoje só o Gemini é usado; os registros e a tabela já têm o campo provedor para
  DeepSeek e OpenAI, que entram pela spec 004. Esta spec não adiciona provedor nem muda a escada.
- **Nível grátis/pago:** como a cobrança do Gemini não está ligada (D10), todo uso é registrado
  como grátis; quando houver chave paga (modo privado, D12), o nível vem de configuração.
- **Assinatura sem número de linha:** para que a mesma falha continue no mesmo grupo entre versões
  (a linha muda a cada edição), a assinatura usa arquivo e função do ponto de origem no código do
  projeto; o número da linha fica no registro, fora da assinatura.
- **Retenção:** log de texto e falhas por tamanho (limite total na ordem de dezenas de MB, valor no
  plano); registros de uso por 12 meses (decidido no clarify de 2026-10-16), estimados em ~150 KB
  por dia de voz intensa, ~55 MB/ano — a estimativa é conferida com o uso real.
- **Resumo de uso:** é uma ferramenta de desenvolvimento/linha de comando (em `tools/`), não um
  painel na interface nem ferramenta de voz (confirmado no clarify de 2026-10-16); cartões no HUD
  ficam para a spec 010.
- **Mensagens ao usuário:** as mensagens de erro que já aparecem na tela e na voz continuam como
  estão; esta spec acrescenta o registro persistente, a linha de escada esgotada no log da tela
  (FR-004a) e a confirmação do `report_problem`. Fazer o JARVIS *falar* que a cota acabou mexeria
  no retorno de várias ações do upstream e ficou de fora.
- **Preços** conferidos em 2026-10-16; o Google anuncia aumentos para 2026-12-01 e 2027-01-01, e a
  tabela editável existe para isso.
- **Dependências:** nenhuma dependência nova de execução é necessária; `pytest` entra como
  dependência de desenvolvimento.
