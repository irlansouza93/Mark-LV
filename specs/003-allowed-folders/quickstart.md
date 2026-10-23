# Quickstart: validar a spec 003

## 1. Testes sem interface (sempre)

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

Esperado: tudo verde, incluindo `tests/test_fs_policy.py` (decisões, negação, links/junções,
`confined`, arquivo enviado, recarga) e `tests/test_fs_policy_actions.py` (cada ação da tabela do
[contrato](contracts/fs_policy.md#mapa-ação--operação) recusa fora das pastas sem tocar o disco).
Testes de link simbólico podem aparecer como `skipped` no Windows sem privilégio; a junção cobre o
mesmo caso.

## 2. Medida de desempenho (SC-005)

`tools/fs_policy_bench.py` cria 10 mil arquivos numa pasta temporária e mede a busca do
`file_controller` com e sem a política (5 rodadas, mediana). Esperado: ≤ 10 % a mais. O número
vai para o PR e para o comentário do porquê em `fs_policy.walk`.

## 3. Interface (⚙ SETUP → ALLOWED FOLDERS)

`py_compile` em `ui.py`; abrir o app, adicionar/mudar/remover uma pasta, conferir
`allowed_folders` em `config/api_keys.json` e que a decisão muda sem reiniciar. Tentar adicionar
`~/.ssh` → recusado com o motivo.

## 4. Teste real (um único roteiro, com a chave, fora do sandbox)

Seguir o "Roteiro de teste real" da [spec](spec.md#roteiro-de-teste-real-princípio-ii) — passos 1
a 7, incluindo o 4b (confirmação ao rodar código). Anotar no PR o resultado de cada passo e as
linhas `kind: "policy"` geradas em `logs/failures.jsonl`.
