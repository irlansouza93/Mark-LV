# Contrato — comando `tools/usage_report.py`

Ferramenta de desenvolvimento (não faz parte do app). Lê `logs/usage-*.jsonl` e imprime o resumo
de uso (FR-016). Nenhuma chamada de rede.

```text
.venv/Scripts/python.exe tools/usage_report.py [--days N | --from YYYY-MM-DD --to YYYY-MM-DD]
                                               [--by day|model|provider] [--json] [--logs DIR]
```

| Opção | Padrão | Efeito |
|---|---|---|
| `--days N` | 14 | últimos N dias locais (o período da decisão em `llm-gestao.md` §6) |
| `--from/--to` | — | período explícito (inclusivo) |
| `--by` | `day` | agrupamento principal da tabela |
| `--json` | off | imprime o `UsageSummary` em JSON (para o Claude Code ler) |
| `--logs DIR` | `logs/` | pasta dos registros (os testes usam uma pasta temporária) |

## Saída em texto (exemplo do formato, valores fictícios)

```text
Uso de LLM — 2026-10-03 a 2026-10-16 (14 dias)   preços conferidos em 2026-10-16

dia         provedor  modelo                         chamadas  cota  tokens in/out     voz min  US$ se pago
2026-10-15  gemini    gemini-3.1-flash-live-preview       212     0  1.84M / 96k           38     2.31
2026-10-15  gemini    gemini-2.5-flash-lite                41     6  52k / 9k               -     0.01
...
Cota grátis esgotou: 2026-10-15 às 14h, 16h (gemini-2.5-flash-lite, gemini-2.5-flash)
Total do período: US$ 14.22 se pago (real: US$ 0.00, nível grátis)
Modelos sem preço na tabela (custo desconhecido): gemini-3.5-flash-lite (17 chamadas)
```

Código de saída: 0 com ou sem registros (sem registros imprime "nenhum registro no período");
2 para argumentos inválidos. Linhas JSON corrompidas são ignoradas e contadas no rodapé.
Termina em < 5 s para 14 dias de registros (SC-005).
