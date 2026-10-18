# Contrato — ferramenta `report_problem` (declaração para o modelo)

Arquivo: `actions/report_problem.py`, auto-descoberto por `core/action_loader.py`.
Classe de risco (Princípio IV): **leitura** — só grava o log local do próprio app.
Bloqueio (Princípio III): bloqueante, instantâneo (< 5 ms; o handler só põe na fila).

## Declaração (alvo ≤ 300 caracteres somados — FR-018)

```python
TOOL = {
    "name": "report_problem",
    "description": "Call FIRST whenever the user says something didn't work "
                   "('não funcionou', 'não deu certo', 'deu errado'), then try to fix it. "
                   "If they didn't say what they expected, ask before calling.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "expected": {"type": "STRING", "description": "What the user wanted, in their words"},
            "happened": {"type": "STRING", "description": "What went wrong"},
        },
        "required": ["expected"],
    },
    "handler": report_problem,
}
```

O tamanho real (descrição + descrições dos parâmetros + nomes) é medido na implementação e
registrado na seção "Custo de tokens" do `plan.md`. Se passar de 300, encurtar a descrição.
Medido em 2026-10-17 com esta redação: **265 caracteres** de texto (449 de JSON compacto).

**Por que a descrição manda "chamar PRIMEIRO"** (1ª rodada do roteiro, 2026-10-17): com a redação
original ("Log a problem the user reports…") o modelo preferiu *resolver* a *anotar* — "Não
funcionou." sem expectativa não gerou pergunta nem anotação, e "não deu certo, eu queria ver a
previsão do tempo" só pesquisou o tempo. A descrição agora ordena a anotação antes da correção,
cita as frases em português que o Irlan usa e diz para perguntar antes quando falta a expectativa.

## Comportamento

- Entrada: `expected` (obrigatório; vazio → `"(not said)"`), `happened` (opcional).
- Efeito: `errlog.record_report(expected, happened)` — grava em `logs/reports.jsonl` com
  transcrição das últimas ~3 trocas **mais a frase em andamento** (`User (now): …` e o rascunho
  `Assistant (now): …` — o relato é chamado no meio do turno e o `main.py` só põe o turno no
  `_session_log` no `turn_complete`), últimas 8 ferramentas e falhas dos últimos 10 min
  (ver [data-model](../data-model.md#problemreport--logsreportsjsonl)).
- Saída para o modelo: `"Logged. Confirm in one short sentence."` — o modelo confirma em voz e
  segue a conversa. Se a gravação não puder ser enfileirada, a saída diz
  `"Could not log it: <motivo>"` (e isso não é contado como soft failure de si mesmo).
- Gatilho: quando o usuário diz que algo não funcionou (clarify Q5; a 1ª rodada mostrou que "só
  quando pede" deixava o modelo resolver sem anotar). O prompt do sistema **não** muda.

## Roteiro de casos (comportamento do modelo — Princípio II, teste real com a chave)

| # | Usuário diz | Esperado |
|---|---|---|
| 1 | "Não funcionou, eu queria que abrisse o Excel com a planilha" | chama `report_problem(expected="abrir o Excel com a planilha", …)`, confirma curto |
| 2 | "Não funcionou" | pergunta em uma frase o que era esperado; registra com a resposta |
| 3 | "Anota esse problema: a câmera não abriu" | chama com `expected` ≈ "câmera abrir" |
| 4 | "Funcionou, obrigado" | **não** chama |
| 5 | "O Excel não abre" (pedido de ajuda, não relato) | tenta resolver; não registra sem pedido |

### Resultado da 1ª rodada (2026-10-17, voz real, descrição original)

| Caso | Resultado |
|---|---|
| 1 | ok na 1ª vez; **a repetição falhou**: "não deu certo, eu queria ver a previsão do tempo" só pesquisou, sem anotar |
| 2 | **falhou**: "Não funcionou." sem expectativa não gerou pergunta nem anotação |
| 3, 4, 5 | ok |

Além disso, os dois relatos gravados vieram **sem a frase que os disparou** na transcrição (o
turno em andamento ainda não estava no `_session_log`). Correções feitas: `errlog.note_speech`
guarda as transcrições do turno corrente e `record_report` as acrescenta, e a descrição foi
reescrita (acima). A 2ª rodada repete os casos 1, 2 e 1 de novo.
