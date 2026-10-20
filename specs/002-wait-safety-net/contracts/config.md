# Contract — chave `wait_cue` em `config/api_keys.json`

```json
"wait_cue": {
    "enabled": true,
    "thinking_ms": 1500,
    "phrase_ms": 4000,
    "give_up_ms": 20000
}
```

- A chave é **opcional**: ausente = padrões acima (rede ligada desde a instalação, FR-010).
- `memory/config_manager.get_wait_cue() -> dict` devolve sempre os quatro campos, já validados
  (faixas e ordem em [data-model.md](../data-model.md#limits-configuração-imutável-durante-um-episódio)),
  no mesmo estilo de `get_turn_tuning()`. Leitura só; não há `save_` (sem botão na interface,
  fora de escopo).
- Docstring da função explica o que cada limite faz e que a recalibração vem do
  `tools/usage_report.py` (seção "Espera da voz").
- Mudança vale na próxima conexão da sessão (como `turn_tuning`).
