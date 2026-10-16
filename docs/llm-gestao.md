# Gestão de LLMs no Mark LV — economia máxima com Gemini grátis, DeepSeek e OpenAI

> Análise de 2026-10-16. Substitui a recomendação anterior ("ligar a cobrança do Gemini já"), que
> não tinha avaliado o nível gratuito nem os créditos que o Irlan já tem. Preços conferidos nas
> páginas oficiais nesta data (fontes no fim); revise antes de fixar orçamento — o Google anuncia
> aumentos para 2026-12-01 e 2027-01-01.

## 1. Como o LV usa LLM hoje

| Uso | Onde | Modelo | Observação |
|---|---|---|---|
| Conversa por voz | `main.py` (`LIVE_MODEL`) | `gemini-3.1-flash-live-preview`, reserva `gemini-2.5-flash-native-audio` | Áudio nativo; o coração do produto |
| Chamadas avulsas FAST/SMART (screenshot, código, documento, YouTube, voos, desktop) | [`core/gemini.py`](../core/gemini.py) | Escada: **sessão Live descartável primeiro**, depois 8 modelos de texto | ~12 pontos de chamada em `actions/` e `main.py` |
| Pesquisa com fontes (SEARCH) | `actions/web_search.py` | 4 modelos REST com *grounding* | O Live não devolve fontes |

**O criador desenhou tudo para o nível gratuito, e desenhou bem.** O pool de cota do Live é
separado do pool de texto; como o pool de texto é o que esgota numa tarde, as chamadas avulsas vão
primeiro para uma sessão Live descartável (medido: 1,7–2,8 s para JSON curto, contra 0,76 s no REST
— mais lento, mas não acaba). Modelo que dá 429/503 descansa 5 min (`_cool`), e a escada soma as
cotas diárias de 8 modelos. Ou seja: **a viabilidade do grátis já foi provada no uso real** — o
Irlan tem razão em não querer trocar por pago sem necessidade.

## 2. O que cada provedor oferece (2026-10-16)

### Gemini (Google)
- **Grátis** em todos os modelos que o LV usa, inclusive Live (3.1 Flash Live, 2.5 Native Audio e o
  novo **Gemini 3.8 Live**), Flash, Flash-Lite e Embedding 2. **Geração de imagem não tem mais
  nível grátis.**
- Limites **por projeto, não por chave**; RPD zera à meia-noite do Pacífico (4–5h em Brasília).
  O Google não publica mais os números na documentação: o valor real do seu projeto aparece no AI
  Studio. Modelos *preview* têm limites menores.
- **Custo do grátis: os dados.** No nível gratuito o conteúdo é "usado para melhorar os produtos"
  (pode ter revisão humana); no pago, não.
- Pago, por 1M tokens: Live 3.1/3.8 entrada US$ 0,75 texto / 3,00 áudio, saída 4,50 / 12,00;
  2.5 Flash-Lite 0,10 / 0,40; 2.5 Flash 0,30 / 2,50; Embedding 2 0,20.

### DeepSeek (seus créditos)
- `deepseek-flash` (V4.1, 1M de contexto, visão, ferramentas, JSON) e `deepseek-v4-pro`
  (raciocínio mais forte, sem visão).
- Por 1M tokens, **fora do pico**: Flash entrada 0,15 (cache: **0,003**), saída 0,60; Pro 0,66 /
  1,98. No pico, o dobro. **O pico é 01–04h e 06–10h UTC = 22–01h e 03–07h em Brasília**: o dia
  útil brasileiro inteiro cai fora do pico.
- Busca/leitura de páginas: US$ 0,75 por mil requisições.
- **Privacidade:** a política da DeepSeek indica armazenamento na China. Não mandar dado pessoal
  sensível (finanças, saúde, memória pessoal) — conferir a política vigente antes de mudar isso.

### OpenAI (seus créditos)
- Texto barato, por 1M tokens: `gpt-5-nano` 0,05 / 0,40; `gpt-5-mini` 0,25 / 2,00; Batch e Flex
  pela metade.
- Embeddings: `text-embedding-3-small` **0,02** (memória inteira de anos custa centavos).
- Voz em tempo real: `gpt-realtime-mini` áudio 10,00 / 20,00 — **3 a 4 vezes o Gemini Live pago**;
  não compensa para a conversa.
- **Privacidade:** dados da API não são usados para treino por padrão. É o destino natural para
  dado pessoal sensível.
- Créditos pré-pagos costumam ter validade (conferir a data no painel de cobrança): gastar antes
  de vencer entra no planejamento.

### Claude
Fica no **desenvolvimento** (Claude Code com o seu plano) e na auditoria de projetos (spec de
auditoria), não como cérebro do JARVIS.

## 3. Custo de uma chamada típica (2 mil tokens de entrada, 500 de saída)

| Provedor / modelo | Custo por chamada | 1.000 chamadas |
|---|---|---|
| Gemini grátis (Live descartável ou texto) | US$ 0 | US$ 0 (até o limite) |
| OpenAI `gpt-5-nano` | US$ 0,0003 | US$ 0,30 |
| DeepSeek Flash, fora do pico (sem cache) | US$ 0,0006 | US$ 0,60 |
| OpenAI `gpt-5-mini` | US$ 0,0015 | US$ 1,50 |
| DeepSeek V4 Pro, fora do pico | US$ 0,0023 | US$ 2,30 |

Conclusão: usar DeepSeek/OpenAI **só como reserva** quando o Gemini grátis esgota custa centavos
por mês. O dinheiro de verdade só apareceria na **voz paga** (estimativa grosseira: US$ 1–3 por
hora de conversa no Gemini Live pago, porque o contexto é recontado a cada turno — **precisa ser
medido**).

## 4. Proposta: roteador por tipo de tarefa e sensibilidade

Regra geral: **grátis primeiro, barato depois, nunca dado sensível onde ele pode virar treino.**

| Classe de tarefa | Ordem de tentativa | Por quê |
|---|---|---|
| Conversa por voz (geral) | Gemini Live grátis | Insubstituível em latência e custo |
| Conversa por voz com **assunto sensível** (finanças, saúde) | ver §5 | Conflito real entre grátis e privacidade |
| Avulsa FAST/SMART **não sensível** | Escada Gemini atual → DeepSeek Flash → `gpt-5-nano` | Grátis enquanto durar; reserva quase de graça |
| Pesquisa com fontes | Gemini SEARCH → busca DuckDuckGo já existente → DeepSeek Search | Mantém fontes citáveis |
| Documento/código longo, não sensível | DeepSeek Flash (1M de contexto, cache barato) → V4 Pro para raciocínio difícil | Melhor custo por contexto grande |
| **Dado pessoal sensível** (lançamento financeiro, saúde, extração de memória pessoal, e-mail) | OpenAI `gpt-5-nano`/`mini` → (opcional) modelo local via `core/llm_client.py` | Sem treino por padrão; centavos |
| Embeddings da memória pessoal | OpenAI `text-embedding-3-small` | Privado e praticamente de graça |
| Tarefas em lote sem pressa (resumo semanal, destilação mensal) | OpenAI Batch/Flex ou DeepSeek fora do pico | Metade do preço |

Como entra no código, sem quebrar o upstream:
- `core/gemini.py` fica como está (é do upstream). Um módulo novo, `core/llm_router.py`, recebe
  `task_class` e `sensitive=True/False`, usa `core/gemini.py` para os degraus Gemini e um cliente
  único compatível com OpenAI (DeepSeek e OpenAI usam o mesmo formato de API; muda só a URL) para
  os outros.
- **Teto mensal por provedor** em `config/api_keys.json` (ex.: DeepSeek US$ 2, OpenAI US$ 3), com
  aviso a 80% e parada a 100%; contador de tokens e custo persistido.
- Chamada com `sensitive=True` **nunca** cai em Gemini grátis nem DeepSeek, mesmo que o resto
  falhe: falha explícita é melhor que vazamento.
- Chamadas existentes migram aos poucos; recurso novo (Notion, finanças, memória) já nasce no
  roteador.

## 5. O ponto que exige decisão: voz + dado sensível

Tudo o que você **fala** para o JARVIS passa pela sessão Live. No nível grátis, dizer "gastei 230
reais no remédio X" vai para o Google como dado que pode ser usado para treino — **mesmo que o
lançamento depois seja processado na OpenAI**. Três saídas possíveis:

1. **Modo privado sob demanda (recomendado para avaliar):** um segundo projeto Google com cobrança
   ligada e teto baixo. Quando o assunto é sensível ("modo privado", ou automaticamente ao abrir
   finanças/saúde), a sessão reconecta com a chave paga (trocar voz já reconstrói a sessão, então o
   mecanismo existe). Paga-se só os minutos privados.
2. **Sensível só por texto/tela:** finanças e saúde lançadas pelo Notion/painel, nunca por voz.
   Custo zero, menos mágico.
3. **Aceitar o grátis para tudo:** mais simples, mas contraria o Princípio VIII da constituição.

Não usar vários projetos grátis para multiplicar cota: contorna os limites do Google e arrisca a
conta.

## 6. Como decidir com dados (não com suposição)

A spec 001 (registro de erros) passa a registrar também **uso de LLM**: provedor, modelo, tokens de
entrada/saída (o Gemini devolve `usage_metadata`, inclusive no Live), latência, 429/503 e custo
calculado pela tabela de preços. Depois de duas semanas de uso real:
- quantas vezes por dia a escada grátis esgotou e em que horário;
- quanto a reserva DeepSeek/OpenAI teria custado;
- quantos minutos de voz por dia (para estimar o modo privado).

Só então se decide o teto de cada provedor e se o modo privado vale.

## Fontes
- [Gemini API — Pricing](https://ai.google.dev/gemini-api/docs/pricing) (atualizada 2026-10-15)
- [Gemini API — Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) (atualizada 2026-10-15)
- [DeepSeek — Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [OpenAI — API Pricing](https://developers.openai.com/api/docs/pricing)
