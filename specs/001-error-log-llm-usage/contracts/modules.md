# Contrato — módulos `core/` novos

Assinaturas que o resto do app (e os testes) usam. **Toda função pública de registro nunca
levanta exceção e retorna em < 1 ms** (só põe na fila). Funções marcadas *(pura)* não fazem I/O.

## `core/redact.py`

```python
def redact(value, secrets: Iterable[str] = ()) -> Any         # (pura) str/dict/list → cópia mascarada
def mask(secret: str) -> str                                   # (pura) "‹redacted:AIza…f3›"
def load_config_secrets(path: Path) -> list[str]               # valores-string ≥ 16 chars do api_keys.json
```

## `core/errlog.py`

```python
def setup(log_dir: Path = ROOT / "logs", *, tee_console: bool = True) -> None
    # idempotente: cria logs/, liga a thread de escrita, o log de texto rotativo, o tee de
    # stdout/stderr, sys.excepthook/threading.excepthook, limpa usage-* com > 12 meses, lê a versão.
def bind(*, notify: Callable[[str], None] | None = None,
         transcript: Callable[[], list[str]] | None = None) -> None
def record_failure(component: str, name: str, *, exc: BaseException | None = None,
                   error_type: str | None = None, message: str = "", phase: str | None = None,
                   context: dict | None = None, origin: str | None = None) -> None
def note_tool(name: str, args: dict, result: object) -> None   # anel de ferramentas + soft failure
def record_report(expected: str, happened: str | None = None) -> None
def notify_once(key: str, message: str, every_s: float = 300) -> None
def set_live_session(session_id: str | None) -> None
def version() -> str
def flush(timeout_s: float = 1.0) -> None                      # usado no atexit e nos testes

# puras
def signature(component, name, error_type, origin) -> str
def innermost_project_frame(tb, root: Path) -> tuple[str, int] | None
def classify_soft_failure(result: str) -> str | None          # "soft:could not save" | None
def group(records: Iterable[dict]) -> list[dict]
def parse_version(git_head: str | None, ref_text: str | None, packed_refs: str | None,
                  changelog: str | None) -> str
def months_to_delete(filenames: Iterable[str], today: date, keep_months: int = 12) -> list[str]
class BurstLimiter:  # (pura, relógio injetado) allow(signature, now) -> bool; drain_suppressed(now)
def quota_reset_local(now_utc: datetime, local_tz) -> datetime   # meia-noite do Pacífico → local (R11)
```

## `core/llm_usage.py`

```python
def record_oneshot(model: str, tier: str | None, result: str, latency_ms: int | None,
                   usage_metadata=None) -> None
def record_tts(model: str, result: str, latency_ms: int | None, usage_metadata=None) -> None
def live_session_start(model: str, voice: str, resumed: bool) -> None
def live_message(response) -> None          # chamado a cada LiveServerMessage; ignora o que não interessa
def live_session_end(reason: str) -> None
def load_prices(path: Path = ROOT / "config" / "llm_prices.json") -> dict

# puras
def tokens_from_usage(usage_metadata) -> dict | None           # SDK (REST ou Live) → campo `tokens`
def cost(prices: dict, provider: str, model: str, tokens: dict | None,
         when: datetime | None = None) -> tuple[float | None, bool]   # (custo, price_missing)
class LiveUsageMeter:   # feed(usage_metadata) ; turn_complete() -> dict | None ; regra de R7
def summarize(records: Iterable[dict], tz) -> dict             # UsageSummary
```

## Ganchos nos arquivos existentes (o diff inteiro do upstream é isto)

| Arquivo | Onde | Linha(s) acrescentada(s) |
|---|---|---|
| `main.py` | `main()`, antes de `JarvisUI(...)` | `errlog.setup()` |
| `main.py` | `JarvisLive.run()`, junto de `confirm_gate.bind(...)` | `errlog.bind(notify=self.ui.write_log, transcript=lambda: self._session_log[-6:])` |
| `main.py` | `_execute_tool`, antes do `print(... 📤 ...)` | `errlog.note_tool(name, args, result)` |
| `main.py` | `_execute_tool`, `except Exception as e:` | `errlog.record_failure("inline_tool", name, exc=e, context={"args": args})` |
| `main.py` | `_receive_audio`, topo do loop de mensagens | `llm_usage.live_message(response)` |
| `main.py` | `run()`, depois de `print("[JARVIS] Connected.")` | `llm_usage.live_session_start(live_model, <voz>, _resumed_with)` |
| `main.py` | `run()`, `except BaseException`, depois do bloco de reconexão voluntária | `errlog.record_failure("live", "live_connect", exc=e, phase=…, context={…})` |
| `main.py` | `run()`, `finally:` | `llm_usage.live_session_end(<motivo>)` |
| `main.py` | imports | `from core import errlog, llm_usage` |
| `core/action_loader.py` | `run()` e `discover_actions()`, nos `except` | `errlog.record_failure("action", …)` |
| `core/plugin_loader.py` | `run()` e `discover_plugins()`, nos `except` | `errlog.record_failure("plugin", …)` |
| `core/gemini.py` | `call()`: sucesso, cada `except`, retorno `None`; `_live_turn`: guardar `usage_metadata` | `llm_usage.record_oneshot(...)`, `errlog.record_failure("gemini", ...)`, `errlog.notify_once("ladder", ...)` |
| `core/voice_preview.py` | depois do `generate_content` | `llm_usage.record_tts(...)` |
| `memory/config_manager.py` | junto dos outros `get_*` | `get_llm_billing() -> str` (`"free"` se ausente; research R9) |
| `ui.py` | — | nenhuma mudança |
| `actions/*` | — | nenhum existente muda; só o arquivo novo `actions/report_problem.py` |
