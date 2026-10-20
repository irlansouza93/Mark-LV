# Contract — `core/wait_cue.py`

Módulo de infraestrutura. Nada aqui importa `main.py`, `ui.py` nem PyQt; `google.genai` só é
importado dentro de `voice_preview._synthesise` (na thread do prefetch). Testável sem microfone,
rede ou relógio real.

## Limites

```python
@dataclass(frozen=True)
class Limits:
    enabled: bool = True
    thinking_ms: int = 1500
    phrase_ms: int = 4000
    give_up_ms: int = 20000

    @classmethod
    def from_config(cls, cfg: dict) -> "Limits": ...   # recebe o dict de config_manager.get_wait_cue()
```

## Vigia

```python
class WaitWatchdog:
    def __init__(self, limits: Limits, clock: Callable[[], float] = time.monotonic): ...

    # Eventos (chamados do laço asyncio; O(1), sem I/O, nunca levantam)
    def user_speech(self) -> None          # trecho de input_transcription
    def user_text(self) -> None            # pedido digitado enviado
    def model_audio(self) -> dict | None   # 1º áudio fecha o episódio → VoiceWaitRecord
    def tool_started(self) -> None
    def tool_finished(self) -> None
    def turn_complete(self) -> dict | None # sem áudio → record(no_reply); em EXCHANGE → volta a IDLE
    def cancel(self, reason: str) -> dict | None
    def phrase_played(self, how: str) -> None   # "voice" | "tone" — o main informa o que tocou

    # Tique (a cada ~100 ms)
    def tick(self) -> tuple[list[str], dict | None]
        # ações em ordem, cada uma no máximo uma vez por episódio: "thinking", "phrase", "give_up"
        # + o VoiceWaitRecord quando "give_up" fecha o episódio

    @property
    def waiting(self) -> bool              # episódio aberto (o main usa para recolocar "pensando")
    @property
    def thinking_shown(self) -> bool
```

Garantias testadas:
- resposta antes de `thinking_ms` → nenhuma ação; record `audio` com latência;
- `thinking` em `thinking_ms`, `phrase` em `phrase_ms`, cada um uma vez;
- `turn_complete` sem áudio → record `no_reply`, nenhuma ação depois;
- `user_speech` empurra a âncora; depois do 1º áudio, `user_speech` é ignorado até `turn_complete`;
- desistência só sem ferramenta ativa, contada de máx(âncora, fim da última ferramenta);
- `cancel` fecha com o motivo; `enabled=False` → nunca emite ação, mas devolve records;
- frase nunca é emitida depois do 1º áudio.

## Frases

```python
TEXTS: dict[str, dict[str, str]]   # {"hold": {"pt": "Um momento.", "en": "One moment.", ...},
                                   #  "giveup": {"pt": "Não consegui responder. Pode repetir?", ...}}
RATE = 24000                       # = RECEIVE_SAMPLE_RATE do main

def phrase_path(kind: str, voice: str, lang: str) -> Path      # config/voice_cache/wait_<kind>_<voice>_<lang>.wav
def load_phrase(kind: str, voice: str, lang: str) -> np.ndarray | None   # int16 @ 24 kHz; None se não há cache
def tone() -> np.ndarray                                        # int16 @ 24 kHz, ~300 ms, nunca falha
def prefetch(voice: str, lang: str, on_error: Callable[[str, str], None] | None = None,
             synth: Callable | None = None) -> threading.Thread | None
    # gera o que falta em thread daemon; None se tudo já está em cache.
    # synth injetável para teste (padrão: voice_preview._synthesise com text/style).
def pcm_for(kind: str, voice: str, lang: str) -> tuple[np.ndarray, str]
    # (pcm, "voice") do cache ou (tone(), "tone")
```

## Ganchos no `main.py` (contrato com o código do upstream)

| Onde | Chamada |
|---|---|
| conexão da sessão (após `live_session_start`) | `self._wait = WaitWatchdog(Limits.from_config(get_wait_cue()))`; `wait_cue.prefetch(voice, lang, on_error=…)`; criar `_wait_cue_loop` |
| queda/reconexão da sessão | `cancel("session")` + gravar record |
| `input_transcription` com texto | `user_speech()` |
| `_on_text_command` (quando realmente envia) | `user_text()` |
| `response.data` (não interrompido) | se `_cue_playing`: esvaziar fila, `_cue_playing=False`; `model_audio()` → gravar record |
| `response.tool_call` / após `send_tool_response` | `tool_started()` / `tool_finished()` |
| `sc.turn_complete` | `turn_complete()` → gravar record; se o HUD foi posto em "pensando" pela rede e não há áudio, `ui.set_state("LISTENING")` (se não mudo) |
| `interrupt()` | `cancel("interrupt")` |
| tique: mudo / dormindo | `cancel("muted")` / `cancel("asleep")` |
| ação `thinking` | `ui.set_state("THINKING")` |
| ação `phrase` | `pcm, how = pcm_for("hold", …)`; enfileirar em fatias; `_cue_playing=True`; `phrase_played(how)` |
| ação `give_up` | tocar `giveup` igual; `errlog.record_failure(...)`; `ui.write_log(...)`; HUD → `LISTENING`; gravar record |
| playback, ramo de timeout | `_cue_playing` e fila vazia → `set_speaking(False)`, `_cue_playing=False`; se `self._wait.waiting`: `ui.set_state("THINKING")` |

Gravar record = `llm_usage.record_voice_wait(record)` (não bloqueia; fila do `errlog`).
