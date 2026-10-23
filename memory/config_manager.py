import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def load_api_keys() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Failed to load api_keys.json: {e}")
        return {}

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'JARVIS' if not set."""
    return load_api_keys().get("assistant_name", "JARVIS") or "JARVIS"


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "JARVIS"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Every prebuilt voice the Gemini Live API accepts in `voice_name` — all 30, not
# the five the picker used to offer. They are the same voices the native-audio
# Live models speak with, so none of them is a robotic TTS fallback; they differ
# in timbre and delivery, which is what the one-word character below describes
# (Google's own descriptor). The register is by ear, not from Google — the
# picker plays a sample, so a wrong guess costs one click. Names are proper nouns — identical in every
# language, so the list is safe to show verbatim in any locale.
VOICE_INFO: dict[str, tuple[str, str]] = {
    # name:          (character,        register)
    "Charon":        ("Informative",    "male"),
    "Puck":          ("Upbeat",         "male"),
    "Fenrir":        ("Excitable",      "male"),
    "Orus":          ("Firm",           "male"),
    "Enceladus":     ("Breathy",        "male"),
    "Iapetus":       ("Clear",          "male"),
    "Umbriel":       ("Easy-going",     "male"),
    "Algieba":       ("Smooth",         "male"),
    "Algenib":       ("Gravelly",       "male"),
    "Rasalgethi":    ("Informative",    "male"),
    "Alnilam":       ("Firm",           "male"),
    "Schedar":       ("Even",           "male"),
    "Achird":        ("Friendly",       "male"),
    "Zubenelgenubi": ("Casual",         "male"),
    "Sadachbia":     ("Lively",         "male"),
    "Sadaltager":    ("Knowledgeable",  "male"),
    "Kore":          ("Firm",           "female"),
    "Aoede":         ("Breezy",         "female"),
    "Zephyr":        ("Bright",         "female"),
    "Leda":          ("Youthful",       "female"),
    "Callirrhoe":    ("Easy-going",     "female"),
    "Autonoe":       ("Bright",         "female"),
    "Despina":       ("Smooth",         "female"),
    "Erinome":       ("Clear",          "female"),
    "Laomedeia":     ("Upbeat",         "female"),
    "Achernar":      ("Soft",           "female"),
    "Pulcherrima":   ("Forward",        "female"),
    "Vindemiatrix":  ("Gentle",         "female"),
    "Sulafat":       ("Warm",           "female"),
    "Gacrux":        ("Mature",         "female"),
}
AVAILABLE_VOICES = list(VOICE_INFO)
DEFAULT_VOICE    = "Charon"

# How each voice sounds, in the user's language, for the VOZ E JEITO cards.
# Written by ear from the samples, so they describe the delivery, not just the
# one-word tag above.
VOICE_DESC_PT: dict[str, str] = {
    "Charon":        "Masculina, informativa e segura. A voz padrão.",
    "Puck":          "Masculina, animada e pra cima.",
    "Fenrir":        "Masculina, empolgada e cheia de energia.",
    "Orus":          "Masculina, firme e decidida.",
    "Enceladus":     "Masculina, baixa e soprada, quase um sussurro.",
    "Iapetus":       "Masculina, clara e bem articulada.",
    "Umbriel":       "Masculina, tranquila e de boa.",
    "Algieba":       "Masculina, macia e sem pressa.",
    "Algenib":       "Masculina, rouca e grave.",
    "Rasalgethi":    "Masculina, de locutor, informativa.",
    "Alnilam":       "Masculina, firme e autoritária.",
    "Schedar":       "Masculina, equilibrada e constante.",
    "Achird":        "Masculina, amigável e próxima.",
    "Zubenelgenubi": "Masculina, casual e descontraída.",
    "Sadachbia":     "Masculina, viva e expressiva.",
    "Sadaltager":    "Masculina, de quem entende do assunto.",
    "Kore":          "Feminina, firme e confiante.",
    "Aoede":         "Feminina, leve e arejada.",
    "Zephyr":        "Feminina, brilhante e alegre.",
    "Leda":          "Feminina, jovem e leve.",
    "Callirrhoe":    "Feminina, tranquila e de boa.",
    "Autonoe":       "Feminina, brilhante e otimista.",
    "Despina":       "Feminina, macia e fluida.",
    "Erinome":       "Feminina, clara e precisa.",
    "Laomedeia":     "Feminina, animada e pra cima.",
    "Achernar":      "Feminina, suave e delicada.",
    "Pulcherrima":   "Feminina, direta e assertiva.",
    "Vindemiatrix":  "Feminina, calma e gentil.",
    "Sulafat":       "Feminina, calorosa e próxima.",
    "Gacrux":        "Feminina, madura e serena.",
}


# ── Personality ("jeito") ─────────────────────────────────────────────────────
# How the assistant behaves, independent of how it sounds. Each entry is
# (card title, card description — both pt-BR, shown to the user — and the
# instruction the model receives). The instruction is English like the rest of
# the system prompt, and always bounded: tone changes, accuracy and tools never
# do, and no persona is allowed to actually demean the user.
PERSONAS: dict[str, tuple[str, str, str]] = {
    "equilibrado": ("Equilibrado", "Direto e cordial. O padrão.", ""),
    "amigo": ("Amigo", "Caloroso, pergunta de volta, se importa com você.",
              "Talk like a close, warm friend: casual, genuinely interested in "
              "the user, ask how they are, follow up on what they share and "
              "celebrate their wins."),
    "brincalhao": ("Brincalhão", "Bem-humorado, solta piadas e provocações leves.",
                   "Be playful and witty: light jokes and friendly teasing in "
                   "most replies, without ever getting in the way of the answer."),
    "profissional": ("Profissional", "Formal e objetivo, trata por senhor. Zero piada.",
                     "Be strictly professional and formal: precise wording, polite "
                     "distance, no jokes, no small talk."),
    "mordomo": ("Mordomo", "Elegante e cerimonioso, com ironia fina.",
                "Be an impeccable butler in the classic JARVIS manner: elegant, "
                "ceremonious, unflappable, with a dry and very subtle irony."),
    "direto": ("Direto", "Só o essencial. Nenhuma palavra a mais.",
               "Be extremely terse: answer with the essential fact or confirmation "
               "only — no preamble, no follow-up offers, no filler."),
    "motivador": ("Motivador", "Energia lá em cima, empurra você pra frente.",
                  "Be an energetic motivator: upbeat, encouraging, push the user "
                  "forward and celebrate every bit of progress."),
    "professor": ("Professor", "Explica com calma, dá exemplos, confere se entendeu.",
                  "Be a patient teacher: explain step by step, give a concrete "
                  "example, and check that the user understood."),
    "zen": ("Zen", "Calmo e pausado. Baixa a sua ansiedade.",
            "Be calm and soothing: slow, gentle, reassuring phrasing that lowers "
            "the user's stress."),
    "sarcastico": ("Sarcástico", "Ácido e irônico, comenta o óbvio. Mas ajuda.",
                   "Be sarcastic and dry: comment on the obvious with mock disdain "
                   "and irony — while still doing exactly what was asked, correctly."),
    "ranzinza": ("Ranzinza", "Resmunga de tudo, reclama — mas sempre resolve.",
                 "Be a grumpy, reluctant helper: grumble and complain about the "
                 "request, then solve it properly anyway."),
    "malandro": ("Malandro", "Gíria, ginga, chama você de parceiro.",
                 "Be street-smart and laid back: heavy slang of the language you "
                 "are speaking (Brazilian slang in Portuguese), call the user "
                 "'partner' or the local equivalent."),
    "narrador": ("Narrador", "Narra o que você faz como locutor de futebol.",
                 "Narrate what the user is doing like an over-excited football "
                 "commentator — then give the actual answer."),
    "carinhoso": ("Carinhoso", "Afetuoso e atencioso, elogia e cuida de você.",
                  "Be affectionate and caring: warm compliments, check on the "
                  "user's wellbeing, sweet but never clingy."),
    "rigoroso": ("Rigoroso", "Cobra, aponta erros, não passa a mão na cabeça.",
                 "Be demanding and exacting: point out mistakes plainly, hold the "
                 "user to their goals, no sugar-coating."),
}
DEFAULT_PERSONA = "equilibrado"


def get_persona() -> str:
    v = str(load_api_keys().get("persona", DEFAULT_PERSONA)).strip().lower()
    return v if v in PERSONAS else DEFAULT_PERSONA


def save_persona(key: str) -> None:
    k = str(key or "").strip().lower()
    _save_flag("persona", k if k in PERSONAS else DEFAULT_PERSONA)


def persona_instruction(key: str | None = None) -> str:
    """The [PERSONALITY] block for the system prompt, or "" for the default."""
    k = key or get_persona()
    instr = PERSONAS.get(k, PERSONAS[DEFAULT_PERSONA])[2]
    if not instr:
        return ""
    return ("[PERSONALITY]\n" + instr + "\nThis changes only your tone and style. "
            "Accuracy, tool use and honesty stay exactly the same, and you never "
            "genuinely insult or demean the user.\n")


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def get_llm_billing() -> str:
    """Whether the Gemini key is on the free or the paid tier (``"free"`` | ``"paid"``).
    Only used to label usage records; absent or invalid means free."""
    v = load_api_keys().get("llm_billing", "free")
    return v if v in ("free", "paid") else "free"


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


# Every centrepiece the HUD can draw, in picker order, with its display label.
# The holographic styles render through core/holo_engine (GPU when available);
# "core" is the original reactor. Any OBJ/GLB in assets/avatars/ is added at
# runtime as "model:<name>" — see hud_styles().
HUD_STYLE_INFO: dict[str, str] = {
    "face":    "HOLO FACE",
    "orb":     "ENERGY ORB",
    "holo":    "PROJECTOR",
    "core":    "REACTOR CORE",
}
HUD_STYLES = tuple(HUD_STYLE_INFO)


def hud_styles() -> dict[str, str]:
    """Built-in styles plus every model dropped into assets/avatars/."""
    out = dict(HUD_STYLE_INFO)
    try:
        from core.mesh_import import discover
        for key in discover():
            out[key] = key.split(":", 1)[1].replace("_", " ").upper()
    except Exception:
        pass
    return out


def get_hud_style() -> str:
    """Which centrepiece the HUD draws — a key of hud_styles().

    Taste, not capability — all of them cost about the same. Defaults to the
    face; an unknown value (a style that was removed, a model file that was
    deleted) falls back to it rather than to an empty HUD.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip()
    if v in HUD_STYLES:
        return v
    return v if v.startswith("model:") and v in hud_styles() else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip()
    _save_flag("hud_style", s if s in hud_styles() else "face")


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


def get_wait_cue() -> dict:
    """When the assistant owes you a sign of life while you wait for a spoken answer.

    ON by default: a reply that takes seconds with no sound and a frozen HUD
    looks exactly like a dead app. Every limit counts from the end of what you
    said (or from sending a typed request) until the first audio of the reply:

    - `thinking_ms`: after this, the HUD shows "thinking" (no sound).
    - `phrase_ms`: after this, a short "one moment" is spoken.
    - `give_up_ms`: with no tool running, after this the assistant says it could
      not answer and asks you to repeat, instead of staying silent forever.

    The defaults (1.5 s / 4 s / 20 s) are a first guess. Recalibrate them from the
    "Espera da voz" section of `tools/usage_report.py`, which measures the real
    wait and suggests limits once there is enough data. A change takes effect on
    the next session connection, like `turn_tuning`. `"enabled": false` turns the
    cues off; the wait is still measured.

    The ordering is enforced after clamping (thinking < phrase < give-up),
    otherwise a hand-edited file could make the phrase fire before the HUD
    changed or the give-up cut off a phrase that never got to play.
    """
    cfg = load_api_keys().get("wait_cue")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _flag(v) -> bool:
        # A hand-edited "false" must not read as truthy; anything unrecognised keeps the net ON.
        if isinstance(v, bool):
            return v
        if isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "off"):
            return False
        return True

    thinking = _int("thinking_ms", 1500, 300, 10000)
    phrase = _int("phrase_ms", 4000, 1000, 30000)
    give_up = _int("give_up_ms", 20000, 5000, 120000)
    if phrase <= thinking:
        phrase = thinking + 500
    if give_up <= phrase:
        give_up = phrase + 1000
    return {
        "enabled":     _flag(cfg.get("enabled", True)),
        "thinking_ms": thinking,
        "phrase_ms":   phrase,
        "give_up_ms":  give_up,
    }


def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data[key] = bool(value) if isinstance(value, bool) else value
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")

# â”€â”€ Allowed folders (spec 003) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# The list of folders the assistant may open, and whether it may change them. Read by
# core/fs_policy (which caches it; save_allowed_folders() tells it to reload). Shape and
# rules: specs/003-allowed-folders/data-model.md. Never "everything open": a damaged list
# degrades to the default (the user's folder), and an unknown mode degrades to read-only.

_allowed_error: str | None = None


def _default_allowed_folders() -> list[dict]:
    return [{"path": str(Path.home()), "mode": "rw"}]


def _fail_allowed(message: str) -> None:
    try:
        from core import errlog
        errlog.record_failure(component="config", name="allowed_folders",
                              error_type="config:allowed_folders", message=message)
    except Exception:
        pass


def _clean_allowed_folders(raw) -> tuple[list[dict], bool]:
    """Normalise a raw list: (clean items, whether anything had to be thrown away)."""
    import os
    out: dict[str, dict] = {}
    dropped = False
    for it in raw:
        p = it.get("path") if isinstance(it, dict) else None
        if not isinstance(p, str) or not p.strip() or "\0" in p:
            _fail_allowed(f"dropped an invalid allowed_folders item: {str(it)[:80]}")
            dropped = True
            continue
        mode = it.get("mode")
        if mode not in ("ro", "rw"):
            _fail_allowed(f"unknown mode {str(mode)[:20]!r} for a folder: using read-only")
            mode = "ro"
        try:
            norm = os.path.realpath(os.path.expanduser(p))
        except (OSError, ValueError):
            _fail_allowed("dropped an allowed_folders item with an unusable path")
            dropped = True
            continue
        item = {"path": norm, "mode": mode}
        if isinstance(it.get("label"), str) and it["label"].strip():
            item["label"] = it["label"].strip()
        key = os.path.normcase(norm)
        if key in out:                      # duplicate: the last mode wins, the first slot stays
            out[key] = {**out[key], **item}
        else:
            out[key] = item
    return list(out.values()), dropped


def get_allowed_folders() -> list[dict]:
    """The folders the assistant may open: [{"path", "mode": "ro"|"rw", "label"?}]."""
    global _allowed_error
    _allowed_error = None
    data = load_api_keys()
    if "allowed_folders" not in data:
        return _default_allowed_folders()       # not written: a future default reaches everyone
    raw = data["allowed_folders"]
    if not isinstance(raw, list):
        _allowed_error = "allowed_folders in api_keys.json is damaged; using your user folder."
        _fail_allowed("allowed_folders is not a list")
        return _default_allowed_folders()
    clean, _dropped = _clean_allowed_folders(raw)
    if not clean:
        if raw:
            _allowed_error = "allowed_folders in api_keys.json is damaged; using your user folder."
        return _default_allowed_folders()
    return clean


def allowed_folders_error() -> str | None:
    """Set when the last read fell back to the default because the stored list was damaged."""
    return _allowed_error


def save_allowed_folders(items: list[dict]) -> str | None:
    """Store the list. Returns the reason it was refused, or None when saved."""
    global _allowed_error
    from core import fs_policy
    clean, _dropped = _clean_allowed_folders(items if isinstance(items, list) else [])
    if not clean:
        return "Add at least one folder."
    for it in clean:
        why = fs_policy.validate_folder(it["path"])
        if why:
            return f'{it["path"]}: {why}'
    _patch_config(allowed_folders=clean)
    _allowed_error = None
    fs_policy.reload()
    return None
