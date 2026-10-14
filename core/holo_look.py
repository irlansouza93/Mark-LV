"""
How a hologram looks and moves — the knobs behind ⚙ SETUP → HOLOGRAMA.

One downloaded head is not another: Angelica's mouth sits lower than Shuri's
mask lets hers show, a scan has no eye meshes, a stylised bust has a skull
twice the usual size. A single global look made every model imitate the
built-in head, so each style now carries its own *look*: render mode, colour
(theme ↔ the model's own texture), effects, and its anatomy — where the eyes
and mouth are, how far the jaw drops, how the eyes glow.

A look is a flat dict of the keys in `PARAMS`. `resolve(style, anatomy)`
builds the one to use: the saved look for that style if there is one,
otherwise the default preset, with any anatomy key left at None filled from
what `core.mesh_import` measured on the model. Saved looks and user presets
live in `config/holo_looks.json`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

LOOKS_FILE = Path(__file__).resolve().parent.parent / "config" / "holo_looks.json"
_lock = threading.Lock()

# (key, label, min, max, default, group) — sliders, in panel order. Labels are
# pt-BR: the panel was asked for in Portuguese (as VOZ E JEITO).
PARAMS: list[tuple[str, str, float, float, float, str]] = [
    # Estilo
    ("surface",       "Superfície (malha)",          0.0, 1.5, 0.0,  "ESTILO"),
    ("points",        "Partículas",                  0.0, 1.5, 1.0,  "ESTILO"),
    ("point_size",    "Tamanho das partículas",      0.4, 3.0, 1.0,  "ESTILO"),
    ("lines",         "Linhas de estrutura",         0.0, 1.5, 1.0,  "ESTILO"),
    # Cor
    ("tex_mix",       "Cor original da textura",     0.0, 1.0, 0.0,  "COR"),
    ("tex_detail",    "Detalhe da textura",          0.0, 1.0, 0.7,  "COR"),
    ("tex_contrast",  "Contraste da textura",        0.4, 2.5, 1.0,  "COR"),
    ("brightness",    "Brilho geral",                0.2, 2.5, 1.0,  "COR"),
    ("fill",          "Preenchimento",               0.0, 1.0, 0.35, "COR"),
    ("rim",           "Borda (fresnel)",             0.0, 2.5, 1.0,  "COR"),
    ("rim_power",     "Fineza da borda",             1.0, 6.0, 3.0,  "COR"),
    ("rim_accent",    "Borda na cor de destaque",    0.0, 1.0, 0.3,  "COR"),
    ("inner",         "Translucidez (ver por dentro)", 0.0, 0.8, 0.10, "COR"),
    # Efeitos
    ("slices",        "Faixas de varredura",         0.0, 2.0, 1.0,  "EFEITOS"),
    ("slice_density", "Densidade das faixas",        3.0, 40.0, 14.0, "EFEITOS"),
    ("slice_speed",   "Velocidade das faixas",      -1.5, 1.5, 0.35, "EFEITOS"),
    ("sweep",         "Onda de energia",             0.0, 1.5, 0.3,  "EFEITOS"),
    ("noise",         "Ruído digital",               0.0, 1.0, 0.0,  "EFEITOS"),
    ("flicker",       "Cintilação",                  0.0, 1.0, 0.3,  "EFEITOS"),
    ("glitch",        "Glitch",                      0.0, 1.0, 0.6,  "EFEITOS"),
    ("scan",          "Linhas de tela",              0.0, 0.5, 0.16, "EFEITOS"),
    ("bloom",         "Bloom",                       0.0, 1.5, 0.42, "EFEITOS"),
    ("exposure",      "Exposição",                   0.3, 2.0, 0.82, "EFEITOS"),
    ("aberration",    "Aberração cromática",         0.0, 3.0, 1.0,  "EFEITOS"),
    ("base",          "Base do projetor",            0.0, 1.5, 1.0,  "EFEITOS"),
    # Olhos
    ("eye_glow",      "Brilho dos olhos",            0.0, 2.0, 1.0,  "OLHOS"),
    ("eye_size",      "Tamanho do brilho",           0.3, 2.5, 1.0,  "OLHOS"),
    ("blink",         "Piscar",                      0.0, 1.0, 1.0,  "OLHOS"),
    ("gaze",          "Movimento do olhar",          0.0, 2.0, 1.0,  "OLHOS"),
    ("eye_x",         "Olhos — afastamento",         0.05, 0.7, None, "OLHOS"),
    ("eye_y",         "Olhos — altura",             -0.6, 0.7, None, "OLHOS"),
    ("eye_z",         "Olhos — profundidade",       -0.5, 1.2, None, "OLHOS"),
    ("eye_w",         "Olhos — largura da abertura", 0.03, 0.2, None, "OLHOS"),
    ("eye_h",         "Olhos — altura da abertura",  0.015, 0.12, None, "OLHOS"),
    # Boca
    ("jaw",           "Abertura da mandíbula",       0.0, 2.5, 1.0,  "BOCA"),
    ("mouth_glow",    "Brilho da boca ao falar",     0.0, 1.5, 0.45, "BOCA"),
    ("mouth_y",       "Boca — altura",              -1.3, 0.3, None, "BOCA"),
    ("mouth_w",       "Boca — largura",              0.08, 0.7, None, "BOCA"),
    ("chin_y",        "Queixo — altura",            -1.6, -0.4, None, "BOCA"),
    ("pivot_z",       "Pivô da mandíbula (prof.)",  -1.2, 0.6, None, "BOCA"),
    ("lips",          "Lábios (vogais)",             0.0, 2.0, 1.0,  "BOCA"),
    # Luz — the realistic mode's lighting
    ("key_light",     "Luz principal",               0.0, 3.0, 1.6,  "LUZ"),
    ("fill_light",    "Luz de preenchimento",        0.0, 2.0, 0.6,  "LUZ"),
    ("specular",      "Brilho especular",            0.0, 2.0, 0.6,  "LUZ"),
    ("saturation",    "Saturação",                   0.0, 1.6, 1.0,  "LUZ"),
    # Movimento
    ("motion",        "Movimento da cabeça",         0.0, 2.0, 1.0,  "MOVIMENTO"),
]

# Choices that are not sliders.
EYE_STYLES = {"glow": "Brilho", "iris": "Íris", "texture": "Só a textura", "none": "Nenhum"}
# Hologram: light, effects and all. Realistic: a lit, opaque game character —
# no particles, bands, noise or glitch (needs the GPU).
RENDER_MODES = {"holograma": "Holograma", "realista": "Realista (personagem de jogo)"}
CHOICES = {"eye_style": ("glow", EYE_STYLES), "render": ("holograma", RENDER_MODES)}

ANATOMY_KEYS = ("eye_x", "eye_y", "eye_z", "eye_w", "eye_h",
                "mouth_y", "mouth_w", "chin_y", "pivot_z")
GROUPS = ("ESTILO", "LUZ", "COR", "EFEITOS", "OLHOS", "BOCA", "MOVIMENTO")

# Anatomy of the built-in head (measured on core.avatar_mesh) — the fallback
# when a model gives nothing better.
BUILTIN_ANATOMY = {"eye_x": 0.283, "eye_y": 0.079, "eye_z": 0.325,
                   "eye_w": 0.116, "eye_h": 0.05,
                   "mouth_y": -0.54, "mouth_w": 0.22, "chin_y": -1.0, "pivot_z": -0.34}


def _defaults() -> dict:
    d = {k: v for k, _l, _a, _b, v, _g in PARAMS}
    for k, (default, _opts) in CHOICES.items():
        d[k] = default
    return d


# Starting points. "classico" is the look every model had before this panel;
# "jogo" is the game-hologram one (a lit surface with a hard Fresnel rim,
# scanning bands and digital noise, the Cortana / Cyberpunk family);
# "original" keeps the model's own colours as projected light.
PRESETS: dict[str, tuple[str, dict]] = {
    "realista": ("Realista (personagem de jogo)", {
        "render": "realista", "points": 0.0, "lines": 0.0, "surface": 1.0,
        "slices": 0.0, "noise": 0.0, "flicker": 0.0, "glitch": 0.0, "scan": 0.0,
        "aberration": 0.0, "sweep": 0.0, "base": 0.0, "bloom": 0.06,
        "exposure": 1.0, "rim": 0.45, "rim_power": 3.5, "rim_accent": 0.5,
        "eye_style": "texture", "mouth_glow": 0.0, "motion": 0.8}),
    "classico": ("Clássico (partículas)", {}),
    "jogo": ("Jogo (superfície)", {
        "surface": 1.0, "points": 0.2, "point_size": 0.8, "lines": 0.0,
        "fill": 0.22, "rim": 1.15, "rim_power": 2.4, "rim_accent": 0.45,
        "slices": 0.55, "slice_density": 22.0, "noise": 0.25, "flicker": 0.4,
        "inner": 0.10, "tex_detail": 1.0, "tex_contrast": 1.5, "bloom": 0.45,
        "exposure": 0.72, "eye_style": "texture", "mouth_glow": 0.3}),
    "original": ("Cor original", {
        "surface": 1.0, "points": 0.15, "lines": 0.0, "tex_mix": 1.0,
        "tex_detail": 1.0, "fill": 0.75, "rim": 0.9, "rim_accent": 0.35,
        "slices": 0.4, "noise": 0.12, "inner": 0.06, "bloom": 0.4,
        "eye_style": "texture", "mouth_glow": 0.15}),
    "misto": ("Misto (tema + textura)", {
        "surface": 1.0, "points": 0.3, "lines": 0.0, "tex_mix": 0.5,
        "tex_detail": 1.0, "tex_contrast": 1.3, "fill": 0.4, "rim": 1.1, "slices": 0.5,
        "noise": 0.15, "bloom": 0.5, "eye_style": "texture", "mouth_glow": 0.3}),
    "fantasma": ("Fantasma (translúcido)", {
        "surface": 0.7, "points": 0.6, "lines": 0.0, "fill": 0.08, "tex_detail": 1.0,
        "rim": 1.8, "rim_power": 3.5, "inner": 0.45, "slices": 1.2,
        "noise": 0.45, "flicker": 0.6, "bloom": 0.7}),
}


def _read() -> dict:
    try:
        return json.loads(LOOKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data: dict) -> None:
    LOOKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOOKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LOOKS_FILE)


def preset(name: str, anatomy: dict | None = None) -> dict:
    """A full look from a built-in or user preset, anatomy filled in."""
    look = _defaults()
    if name in PRESETS:
        look.update(PRESETS[name][1])
    else:
        look.update(_read().get("presets", {}).get(name, {}))
    return fill_anatomy(look, anatomy)


def fill_anatomy(look: dict, anatomy: dict | None) -> dict:
    an = dict(BUILTIN_ANATOMY)
    an.update({k: v for k, v in (anatomy or {}).items() if v is not None})
    for k in ("lid_r", "lid_g", "lid_b"):          # measured, not a slider
        if k in an:
            look[k] = float(an[k])
    for k in ANATOMY_KEYS:
        if look.get(k) is None:
            look[k] = float(an[k])
    return look


def default_preset(style: str, gpu: bool) -> str:
    # The built-in head was tuned for particles; an imported model reads
    # best as a lit surface, which needs the GPU.
    return "jogo" if (gpu and style.startswith("model:")) else "classico"


def realistic(look: dict) -> bool:
    return look.get("render") == "realista"


def resolve(style: str, anatomy: dict | None, gpu: bool) -> dict:
    """The look to draw `style` with: saved, else its default preset."""
    saved = _read().get("styles", {}).get(style)
    if saved:
        look = _defaults()
        look.update(saved)
        return fill_anatomy(look, anatomy)
    return preset(default_preset(style, gpu), anatomy)


def save(style: str, look: dict) -> None:
    with _lock:
        data = _read()
        data.setdefault("styles", {})[style] = {k: v for k, v in look.items()
                                                if not k.startswith("_")}
        _write(data)


def forget(style: str) -> None:
    with _lock:
        data = _read()
        data.get("styles", {}).pop(style, None)
        _write(data)


def save_preset(name: str, look: dict) -> None:
    """A user preset carries the look but not the anatomy — that belongs to
    one model, and applying it to another would put its eyes in the wrong place."""
    with _lock:
        data = _read()
        data.setdefault("presets", {})[name] = {
            k: v for k, v in look.items() if k not in ANATOMY_KEYS and not k.startswith("_")}
        _write(data)


def user_presets() -> list[str]:
    return sorted(_read().get("presets", {}))
