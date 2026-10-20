# Feature Specification: Aviso de espera garantido ("só um momento")

**Feature Branch**: `002-wait-safety-net`

**Created**: 2026-10-19

**Status**: Draft

**Input**: User description: "Aviso de espera garantido ('só um momento'). O usuário nunca fica em silêncio depois de falar. Hoje isso depende de o modelo obedecer à regra [ACKNOWLEDGE BEFORE A SILENCE] de core/prompt.txt. Rede de segurança determinística, sem depender do modelo: se o turno do usuário terminou e nenhum áudio do modelo chegou em X ms, o HUD mostra o estado 'pensando'; passado outro limite, toca um som curto local. Os limites saem de latências medidas (a spec 001 não mede o tempo fim-da-fala → primeiro áudio: live_turn tem latency_ms nulo; esta feature deve medir e registrar essa latência por turno em logs/ e no tools/usage_report.py). Ver docs/demandas.md §13 e docs/VISAO.md §5 item 2."

## Contexto

Quando o Irlan fala e o JARVIS demora, hoje só há uma proteção: a regra
`[ACKNOWLEDGE BEFORE A SILENCE]` do prompt, que manda o modelo dizer uma frase antes de qualquer
espera. Às vezes o modelo não obedece — principalmente quando a demora é para *escrever* os
argumentos de uma ferramenta — e o usuário fica sem saber se foi ouvido
([`docs/demandas.md` §13](../../docs/demandas.md)). A constituição trata isso como falha
(Princípio III: "Silêncio depois de uma pergunta é falha, não detalhe").

Esta feature cria uma **rede de segurança local**, que não depende do modelo: o próprio app nota
o silêncio e reage (HUD "pensando", depois uma frase curta gravada na voz do JARVIS), e passa a **medir** quanto tempo cada
turno leva até a primeira resposta audível.

**Dado que falta:** a spec 001 deixou o campo de latência dos turnos de voz vazio
(`latency_ms: null` em todos os 35 turnos registrados de 2026-10-17 a 2026-10-18), e o
`jarvis.log` não marca o fim da fala do usuário. Por isso os limites não podem sair só dos dados
de hoje: esta feature começa com valores iniciais e passa a gerar o dado para calibrá-los.

Quem usa o resultado:
- **Irlan**, falando com o JARVIS: deixa de ficar no vazio depois de uma pergunta.
- **Irlan e o Claude Code**, lendo o resumo de uso: veem a latência real por tipo de turno e
  quantas vezes a rede de segurança precisou agir (e, portanto, quantas vezes o modelo não
  avisou sozinho).

## Clarifications

### Session 2026-10-19

- Q: Que som o app toca quando o silêncio passa do segundo limite? → A: Uma frase curta gravada na voz atual do JARVIS ("Um momento."), gerada uma vez e guardada em cache local; sem cache, um tom curto sintetizado no app.
- Q: Como a rede começa, sem latência medida ainda? → A: Ligada desde o primeiro dia com os padrões (HUD 1,5 s, frase 4 s, desistência 20 s); mede desde o primeiro turno; recalibração manual pela sugestão do resumo de uso depois de ~1 semana.
- Q: Em esperas longas, depois do primeiro "Um momento.", há lembretes? → A: Não: a frase toca no máximo uma vez por turno; depois disso o HUD em "pensando" basta.
- Q: O que o usuário percebe quando a espera chega à desistência (20 s sem nada, sem ferramenta)? → A: Uma segunda frase gravada ("Não consegui responder. Pode repetir?"), além do registro da falha e do HUD voltando a ouvir; sem reconectar a sessão.

### Session 2026-10-19 (teste real)

- Observado pelo Irlan: a frase de espera saiu "misturada em inglês e italiano" (transcrita como "A momento."). Medido em seguida: com instrução de estilo o TTS lê a instrução em voz alta ou enche de silêncio (6–10 s); frase curta ("Um instante.") sai em inglês mesmo com o idioma fixado; frase completa sem instrução, com o idioma da fala `pt-BR`, sai em português do Brasil em 2,2 s. → A: as frases são geradas **só com o texto**, com o idioma BCP-47 do usuário (`pt-BR` para português), cada uma com **pelo menos 5 palavras**; áudio gerado fora da faixa de duração esperada é descartado como falha.
- Q: A frase de espera pode variar para não ficar monótona? → A: Sim: várias frases por tipo (5 de espera e 3 de desistência em pt-BR; 3 e 2 nas outras línguas), sorteadas entre as que estão em cache, nunca a mesma duas vezes seguidas.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sinal visual de que foi ouvido (Priority: P1)

O Irlan termina de falar. Se o JARVIS não começar a responder logo, o HUD muda para o estado
"pensando" (o mesmo visual que já existe quando uma ferramenta está rodando), sem esperar o modelo.
Quando a voz do JARVIS começa, o HUD volta ao estado de fala normalmente.

**Why this priority**: É o menor passo que já resolve a dúvida "ele me ouviu?" em todos os
turnos lentos, não custa tokens e não interfere no áudio.

**Independent Test**: Fazer um pedido que leve vários segundos (ex.: "pesquise as notícias de
hoje sobre IA") com o modelo sem dizer nada antes; o HUD deve entrar em "pensando" logo depois do
fim da fala, e sair quando a voz começar.

**Acceptance Scenarios**:

1. **Given** o JARVIS está ouvindo e acordado, **When** o usuário termina de falar e nenhum áudio
   do modelo chega dentro do primeiro limite, **Then** o HUD entra em "pensando".
2. **Given** o HUD está em "pensando" por causa da rede de segurança, **When** o primeiro áudio do
   modelo chega, **Then** o HUD sai de "pensando" e mostra a fala, sem atraso perceptível.
3. **Given** o usuário termina de falar, **When** a resposta em áudio chega antes do primeiro
   limite, **Then** o HUD não pisca "pensando" (nada muda em relação a hoje).

---

### User Story 2 - Frase de espera quando o silêncio se prolonga (Priority: P2)

Se mesmo depois do "pensando" o silêncio continuar até um segundo limite, o app toca localmente
uma frase curta na voz do JARVIS ("Um momento."), já gravada em cache (sem chamada ao modelo na
hora), para avisar quem não está olhando para a tela.

**Why this priority**: O Irlan muitas vezes fala com o JARVIS sem olhar para o HUD; a frase cobre
esse caso. Depende do mesmo detector de silêncio da história 1.

**Independent Test**: Repetir o pedido lento da história 1 sem olhar para a tela; a frase deve
tocar uma vez depois do segundo limite e nunca por cima da voz do JARVIS.

**Acceptance Scenarios**:

1. **Given** o usuário terminou de falar e nenhum áudio chegou, **When** o segundo limite passa,
   **Then** toca a frase de espera, uma vez.
2. **Given** a frase de espera está tocando, **When** o áudio do modelo chega, **Then** a frase é cortada e a voz
   do JARVIS toca normalmente, sem sobreposição audível.
3. **Given** o modelo já disse uma frase de aviso por conta própria, **When** a ferramenta continua
   rodando, **Then** a rede de segurança não toca nada (o usuário já ouviu algo neste turno).

---

### User Story 3 - Latência medida e resumida (Priority: P3)

Cada turno de voz passa a registrar quanto tempo levou entre o fim da fala do usuário e o primeiro
áudio do modelo, se houve ferramenta no meio e se a rede de segurança agiu (HUD, frase de espera, desistência). O resumo de
uso mostra essas latências (mediana e percentis) por tipo de turno e recomenda os limites.

**Why this priority**: É o que permite calibrar os limites com número medido (Princípio IX) e
mostra quantas vezes o modelo deixou de avisar sozinho. Não muda nada para o usuário na hora.

**Independent Test**: Usar o JARVIS por alguns turnos (com e sem ferramenta) e rodar o resumo de
uso; ele deve listar a latência de cada tipo e quantas vezes o HUD e as frases foram acionados.

**Acceptance Scenarios**:

1. **Given** um turno de voz respondido, **When** o turno termina, **Then** o registro de uso
   daquele turno traz a latência até o primeiro áudio, em milissegundos.
2. **Given** vários dias de registros, **When** o resumo de uso é pedido, **Then** ele mostra
   mediana e percentis da latência separados por turnos com e sem ferramenta, a contagem de
   acionamentos da rede de segurança e uma sugestão de limites.
3. **Given** registros antigos sem latência (da spec 001), **When** o resumo é pedido, **Then**
   eles são ignorados no cálculo de latência sem erro.

---

### Edge Cases

- **Microfone mudo ou JARVIS dormindo (palavra de ativação):** nada é enviado ao modelo, então não
  há turno — a rede de segurança não age.
- **Pedido digitado** (texto pela interface ou painel): o fim do turno é o momento do envio; a
  rede vale igual.
- **Ruído confundido com fala:** se o modelo encerra o turno sem responder (nenhum áudio e turno
  concluído), a espera é cancelada na hora; nenhuma frase toca depois disso.
- **O usuário volta a falar** (completa a frase, interrompe): a contagem recomeça do fim da nova
  fala.
- **Ferramenta silenciosa** (ex.: `save_memory`, que responde sem fala): o turno termina sem
  áudio — mesma regra do ruído, sem frase.
- **Ferramenta longa depois de um aviso falado:** algum áudio já chegou neste turno, então a rede
  não age de novo.
- **Ferramenta longa sem aviso nenhum:** o HUD já fica em "pensando" pela ferramenta; a frase toca
  no segundo limite como em qualquer silêncio e **não se repete** depois, por mais que a
  ferramenta demore (o HUD segue em "pensando").
- **Reconexão da sessão** (queda, troca de voz): o HUD mostra o estado da conexão; a rede de
  segurança não toca nenhuma frase enquanto não houver sessão.
- **Interrupção pelo usuário:** o áudio descartado da resposta interrompida não conta como
  "primeiro áudio" do turno seguinte.
- **Silêncio muito longo** (nenhum áudio, nenhuma ferramenta rodando e o turno não termina): ver
  FR-009.
- **Saída de áudio indisponível:** se a frase não puder tocar, o HUD continua sendo o aviso e a
  falha fica registrada, sem travar nada.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: O app MUST detectar localmente o fim de um turno do usuário (fim da fala ou envio de
  um pedido digitado), sem depender de o modelo responder.
- **FR-002**: Se nenhum áudio do modelo chegar até o **limite do HUD** depois do fim do turno, o
  HUD MUST entrar no estado "pensando".
- **FR-003**: Se nenhum áudio do modelo chegar até o **limite da frase**, o app MUST tocar, no
  dispositivo de saída escolhido pelo usuário, uma **frase de espera** curta (ex.: "Só um momento, já vejo isso.",
  no idioma do usuário, com a pronúncia desse idioma — português do Brasil para o Irlan) gravada
  na voz atual do JARVIS — no máximo uma vez por turno. Há várias frases por tipo, sorteadas sem
  repetir a anterior. A frase é
  gerada uma única vez por voz e idioma, fora do caminho do turno, e guardada em cache local;
  tocar não chama o modelo. Se a frase ainda não existir no cache (primeira vez, sem rede, voz
  recém-trocada), toca um tom curto sintetizado no app.
- **FR-004**: A espera MUST ser cancelada, sem tocar nada, assim que acontecer qualquer um destes:
  chega áudio do modelo; o modelo conclui o turno; o usuário interrompe; o
  microfone é silenciado; o JARVIS volta a dormir; a sessão cai. Se o usuário volta a falar
  antes de qualquer resposta, a contagem recomeça do fim da nova fala (não é cancelamento).
- **FR-005**: As frases da rede de segurança (espera e desistência) NUNCA podem tocar por cima da
  voz do JARVIS; se a voz do modelo começar durante uma delas, a frase MUST ser cortada.
- **FR-006**: A rede de segurança MUST NOT acrescentar nada ao que é enviado ao modelo (nenhum
  token a mais por conexão ou por turno) e MUST NOT bloquear os loops de áudio nem a interface.
- **FR-007**: Cada turno de voz MUST registrar, junto do registro de uso já existente, a latência
  entre o fim do turno do usuário e o primeiro áudio do modelo (ou a ausência de áudio), se houve
  ferramenta no turno e se o HUD, a frase de espera e a desistência da rede de segurança foram acionados.
- **FR-008**: O resumo de uso MUST mostrar a latência (mediana, p90, p95) por tipo de turno (com e
  sem ferramenta), a quantidade de turnos em que a rede de segurança agiu e uma sugestão de
  limites calculada a partir dos dados.
- **FR-009**: Se o silêncio passar do **limite de desistência** sem áudio, sem ferramenta
  rodando e sem o turno ser concluído, o app MUST: tocar uma **frase de desistência** gravada na
  voz atual do JARVIS ("Não consegui responder. Pode repetir?", no idioma do usuário; mesmo cache
  e mesmo tom de reserva da FR-003); registrar o caso como falha "mole" no registro de falhas da
  spec 001; mostrar uma linha curta no log da tela; e tirar o HUD de "pensando" (volta a ouvir).
  A sessão NÃO é reconectada por isso. Se o áudio do modelo chegar depois da desistência, ele
  toca normalmente.
- **FR-010**: A rede de segurança MUST vir **ligada desde a instalação**, com os valores padrão
  HUD = 1,5 s, frase = 4 s e desistência = 20 s. Os três limites e o liga/desliga MUST ser
  ajustáveis na configuração local sem mexer em código. O app NÃO ajusta os limites sozinho: a
  recalibração é manual, a partir da sugestão do resumo de uso (FR-008).
- **FR-011**: A lógica de decidir quando acionar o HUD, a frase de espera e a desistência MUST ser testável sem
  microfone, sem rede e sem relógio real.

### Key Entities

- **Turno de voz**: um pedido do usuário e a resposta do modelo. Atributos novos: momento do fim
  do turno do usuário, momento do primeiro áudio do modelo, latência, houve ferramenta, rede de
  segurança acionada (HUD / frase de espera / desistência).
- **Limites da rede de segurança**: limite do HUD, limite da frase, limite de desistência, ligada ou
  desligada. Valores iniciais documentados; recalibrados com o resumo de uso.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No roteiro de teste real, em 100% dos turnos em que o usuário terminou de falar e o
  modelo ia responder, o usuário percebe algum sinal (voz, HUD "pensando" ou frase de espera) até o limite do
  HUD mais 0,2 s, contados do momento em que o app registra o fim da fala.
- **SC-002**: Em turnos cuja resposta começa antes do limite do HUD, o HUD não entra em
  "pensando" (0 ocorrências no roteiro).
- **SC-003**: Nenhuma frase da rede se sobrepõe de forma audível à voz do JARVIS (0 ocorrências no roteiro)
  e nunca toca em turnos que o modelo encerrou sem responder.
- **SC-004**: Pelo menos 95% dos turnos de voz respondidos passam a ter a latência registrada.
- **SC-005**: Os tokens enviados por conexão e por turno não mudam (comparação antes/depois no
  registro de uso), e o tempo por quadro do HUD continua dentro do orçamento atual.
- **SC-006**: Depois de uma semana de uso, o resumo permite responder com números: "quanto tempo o
  JARVIS leva para começar a responder, com e sem ferramenta, e quantas vezes ficou em silêncio
  sem avisar".

## Assumptions

- O HUD já tem o estado "pensando" (usado hoje enquanto uma ferramenta roda); ele é reaproveitado,
  sem estilo visual novo.
- **Valores padrão** (FR-010; ainda sem medição): HUD 1,5 s (sugestão de `docs/demandas.md` §13),
  frase 4 s, desistência 20 s. Recalibração prevista depois de ~1 semana de uso, pela sugestão do
  resumo de uso.
- "Fim do turno do usuário" é medido no próprio app (pelo último trecho de fala reconhecido pelo
  servidor; ver research R1); o modelo de voz só responde depois que o servidor decide que a fala
  acabou, então parte da latência medida é essa decisão do servidor — é o que o usuário sente.
- O registro de latência usa os arquivos de uso que a spec 001 já criou (mesma retenção de 12
  meses); nenhum arquivo novo em `logs/`.
- A regra `[ACKNOWLEDGE BEFORE A SILENCE]` do prompt continua; a rede de segurança é o reforço,
  não a substituta. Mudar o prompt está fora de escopo (é candidato da "dieta do prompt").
- Ligar/desligar pela interface (botão no SETUP) fica fora de escopo; a configuração local basta.
- Funciona igual em Windows, macOS e Linux (as frases usam a mesma saída de áudio que a voz).
