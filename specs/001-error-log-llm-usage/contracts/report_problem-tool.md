# Contrato — ferramenta `report_problem` (declaração para o modelo)

Arquivo: `actions/report_problem.py`, auto-descoberto por `core/action_loader.py`.
Classe de risco (Princípio IV): **leitura** — só grava o log local do próprio app.
Bloqueio (Princípio III): bloqueante, instantâneo (< 5 ms; o handler só põe na fila).

## Declaração (alvo ≤ 300 caracteres somados — FR-018)

```python
TOOL = {
    "name": "report_problem",
    "description": "Log a problem the user reports, e.g. 'that didn't work'. "
                   "If unclear, ask what they expected first.",
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

## Comportamento

- Entrada: `expected` (obrigatório; vazio → `"(not said)"`), `happened` (opcional).
- Efeito: `errlog.record_report(expected, happened)` — grava em `logs/reports.jsonl` com
  transcrição das últimas ~3 trocas, últimas 8 ferramentas e falhas dos últimos 10 min
  (ver [data-model](../data-model.md#problemreport--logsreportsjsonl)).
- Saída para o modelo: `"Logged. Confirm in one short sentence."` — o modelo confirma em voz e
  segue a conversa. Se a gravação não puder ser enfileirada, a saída diz
  `"Could not log it: <motivo>"` (e isso não é contado como soft failure de si mesmo).
- Gatilho: só quando o usuário pede (clarify Q5). O prompt do sistema **não** muda.

## Roteiro de casos (comportamento do modelo — Princípio II, teste real com a chave)

| # | Usuário diz | Esperado |
|---|---|---|
| 1 | "Não funcionou, eu queria que abrisse o Excel com a planilha" | chama `report_problem(expected="abrir o Excel com a planilha", …)`, confirma curto |
| 2 | "Não funcionou" | pergunta em uma frase o que era esperado; registra com a resposta |
| 3 | "Anota esse problema: a câmera não abriu" | chama com `expected` ≈ "câmera abrir" |
| 4 | "Funcionou, obrigado" | **não** chama |
| 5 | "O Excel não abre" (pedido de ajuda, não relato) | tenta resolver; não registra sem pedido |
