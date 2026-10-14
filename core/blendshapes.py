"""
Blend shapes: the way game characters actually talk and blink.

A head made for real-time animation carries its expressions as morph targets
sculpted by an artist — Ready Player Me avatars ship the 15 Oculus visemes
(`viseme_aa`, `viseme_O`…) and, on request, the 52 ARKit shapes (`jawOpen`,
`eyeBlinkLeft`, `eyeLookOutRight`…); VRM/VRoid models carry `Fcl_MTH_A`,
`Fcl_EYE_Close`; VRChat avatars `vrc.v_aa`. When a model has them, nothing is
guessed: the mouth opens the way its artist shaped it, the lids close over the
eyes, and the procedural rig (core.face_rig) stands down for whatever the
shapes cover.

The speech signal is the one the rest of the head uses (core.avatar): how open
(`mouth`), and how spread (+) or rounded (−) the lips are (`wide`), from the
formants of the voice actually playing. They map onto whichever shapes the
model has — an open vowel, a spread one, a rounded one.
"""

from __future__ import annotations

import re

import numpy as np

# role -> name patterns, best first (matched on the lower-cased name with any
# "blendShape1." style prefix removed).
_ROLES = {
    "open":   [r"jawopen", r"viseme_aa", r"v_aa", r"fcl_mth_a", r"mouthopen", r"^a$",
               r"mouth_?open", r"jaw_?open"],
    "wide":   [r"viseme_e", r"v_e", r"fcl_mth_e", r"viseme_i", r"v_ih", r"fcl_mth_i",
               r"mouthsmile$", r"^e$", r"^i$"],
    "round":  [r"viseme_o", r"v_oh", r"fcl_mth_o", r"viseme_u", r"v_ou", r"fcl_mth_u",
               r"mouthfunnel", r"mouthpucker", r"^o$", r"^u$"],
    "blink_l": [r"eyeblinkleft", r"eyeblink_l", r"blink_l$", r"fcl_eye_close_l",
                r"eye_?close_?l"],
    "blink_r": [r"eyeblinkright", r"eyeblink_r", r"blink_r$", r"fcl_eye_close_r",
                r"eye_?close_?r"],
    "blink":  [r"eyesclosed", r"^blink$", r"fcl_eye_close$", r"eyes_?closed"],
    "look_in_l": [r"eyelookinleft"], "look_out_l": [r"eyelookoutleft"],
    "look_in_r": [r"eyelookinright"], "look_out_r": [r"eyelookoutright"],
    "look_up_l": [r"eyelookupleft"], "look_up_r": [r"eyelookupright"],
    "look_dn_l": [r"eyelookdownleft"], "look_dn_r": [r"eyelookdownright"],
}


def _norm(name: str) -> str:
    n = name.lower()
    return n.rsplit(".", 1)[-1] if "." in n else n


class Blendshapes:
    def __init__(self, names, deltas: np.ndarray) -> None:
        self.names = [str(n) for n in names]
        self.deltas = deltas
        norm = [_norm(n) for n in self.names]
        self.role: dict[str, int] = {}
        for role, pats in _ROLES.items():
            for pat in pats:
                hit = next((i for i, n in enumerate(norm) if re.search(pat, n)), None)
                if hit is not None:
                    self.role[role] = hit
                    break

    @property
    def has_mouth(self) -> bool:
        return "open" in self.role

    @property
    def has_blink(self) -> bool:
        return "blink" in self.role or ("blink_l" in self.role and "blink_r" in self.role)

    @property
    def has_gaze(self) -> bool:
        return "look_out_l" in self.role and "look_in_l" in self.role

    def weights(self, mouth: float, wide: float, blink: float, gaze=(0.0, 0.0),
                gain: float = 1.0) -> dict[int, float]:
        w: dict[int, float] = {}

        def put(role, v):
            i = self.role.get(role)
            if i is not None and v > 0.003:
                w[i] = w.get(i, 0.0) + float(min(1.0, v))

        m = max(0.0, mouth) * gain
        put("open", m * (1.0 - 0.45 * abs(wide)))
        put("wide", m * max(0.0, wide))
        put("round", m * max(0.0, -wide))
        if "blink" in self.role and not ("blink_l" in self.role and "blink_r" in self.role):
            put("blink", blink)
        else:
            put("blink_l", blink)
            put("blink_r", blink)
        gx, gy = float(gaze[0]), float(gaze[1])
        # +x is the viewer's right: the subject's left eye looks out, the
        # right eye looks in.
        put("look_out_l", gx); put("look_in_r", gx)
        put("look_in_l", -gx); put("look_out_r", -gx)
        put("look_up_l", -gy); put("look_up_r", -gy)
        put("look_dn_l", gy); put("look_dn_r", gy)
        return w

    def blend(self, base: np.ndarray, w: dict[int, float]) -> np.ndarray:
        out = base.copy()
        for i, v in w.items():
            out += self.deltas[i] * v
        return out


def from_gpu(gpu: dict) -> Blendshapes | None:
    names = gpu.get("morph_names")
    D = gpu.get("morph_pos")
    if names is None or D is None or len(names) == 0:
        return None
    bs = Blendshapes(list(names), np.asarray(D, np.float32))
    return bs if bs.role else None
