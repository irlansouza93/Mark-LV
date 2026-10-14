"""
A face rig for any downloaded head, from where its face actually is.

Why
---
The first rig guessed: mouth height from the profile, eyes from proportions,
a horizontal cut across the face for the jaw. Off by a few millimetres, the
cut opened under the lower lip or between nose and lip — a second mouth —
and a lid covered half an eye. Game characters do not guess: their faces are
rigged by hand or carry blend shapes. For a model with neither, the pipeline
the auto-rigging tools use (MVMP and the like) is:

  1. render the head from the front, lit, as the user will see it;
  2. run MediaPipe's Face Landmarker on that picture — 478 points, including
     the inner contour of the lips and the rim of each eyelid;
  3. carry each point back onto the mesh (the front-most surface sample under
     that pixel).

From those points the rig is exact for that face:

  * **The mouth opens along the lips' own line.** Triangles that bridge the
    upper and the lower lip are removed (most models have their lips welded
    shut), everything under the line follows the jaw with the corners tied,
    and a mouth bag — a dark cavity with upper and lower teeth — sits behind
    the lips, the way game heads are built.
  * **Lids close along each eye's own rim**, in the skin's colour.
  * **Painted eyes look around**: inside the eye the texture shifts with the
    gaze, iris most, edges not at all (eyeball meshes turn instead).

No face found (a mask, a robot) means no rig: the head keeps a gentle jaw and
nothing that could draw a mouth where there is none. MediaPipe is optional;
without it, or without `assets/models/face_landmarker.task`, the same.
Detection runs once per model (~0.3 s, on the GUI thread, it needs the GL
context) and is cached next to the model's cache.
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "assets" / "models" / "face_landmarker.task"

# MediaPipe face-mesh indices. "R" is the subject's right (image left, x < 0).
LIPS_UP = [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308]    # upper inner, corner → corner
LIPS_LO = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308]    # lower inner
LIPS_OUT = [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291,
            375, 321, 405, 314, 17, 84, 181, 91, 146]
EYE_R_UP = [33, 246, 161, 160, 159, 158, 157, 173, 133]
EYE_R_LO = [33, 7, 163, 144, 145, 153, 154, 155, 133]
EYE_L_UP = [362, 398, 384, 385, 386, 387, 388, 466, 263]
EYE_L_LO = [362, 382, 381, 380, 374, 373, 390, 249, 263]
IRIS_R, IRIS_L = 468, 473
CHIN, NOSE = 152, 1
LID_N = 9                         # samples per lid curve sent to the shader

_det = None
_det_lock = threading.Lock()


def available() -> bool:
    if not MODEL.exists():
        return False
    try:
        import mediapipe  # noqa: F401
        return True
    except Exception:
        return False


def _detector():
    global _det
    with _det_lock:
        if _det is None:
            from mediapipe.tasks import python as mpt
            from mediapipe.tasks.python import vision
            _det = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=str(MODEL)),
                num_faces=1, min_face_detection_confidence=0.3,
                min_face_presence_confidence=0.3))
        return _det


# ── detection ─────────────────────────────────────────────────────────────────

def detect(head) -> np.ndarray | None:
    """478 landmark points on `head`'s rest surface (normalised model units),
    or None when no face is found. `head` is a HoloHead with a GPU surface."""
    from PyQt6.QtGui import QColor, QImage, QPainter
    from core import holo_look
    from core.avatar import _CAM_D

    W, R = 900, 330.0
    cx, cy = W / 2.0, W * 0.42
    saved = (head.look, head.motion, head._mouth, head._blink, list(head._gaze),
             head._yaw, head._pitch, head.show_markers, getattr(head, "rig", None))
    try:
        head.rig = None
        lk = holo_look.preset("realista", head.anatomy)
        lk["motion"], lk["base"] = 0.0, 0.0
        head.set_look(lk)
        head._mouth, head._blink, head._gaze = 0.0, 0.0, [0.0, 0.0]
        head._yaw = head._pitch = 0.0
        head.show_markers = False
        img = QImage(W, W, QImage.Format.Format_RGB888)
        img.fill(QColor(40, 40, 44))
        p = QPainter(img)
        head.paint(p, cx, cy, R, QColor(0, 220, 255), QColor(120, 255, 240), QColor(2, 6, 12))
        p.end()
    finally:
        (look, head.motion, head._mouth, head._blink, head._gaze,
         head._yaw, head._pitch, head.show_markers, rig) = saved
        head.rig = rig
        head.set_look(look)

    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, np.uint8).reshape(W, img.bytesPerLine())[:, :W * 3]
    arr = np.ascontiguousarray(arr.reshape(W, W, 3))
    import mediapipe as mp
    res = _detector().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=arr))
    if not res.face_landmarks:
        return None
    uv = np.array([[lm.x * W, lm.y * W] for lm in res.face_landmarks[0]], np.float32)

    # Back onto the surface: the front-most sample under each landmark.
    P = head._sp
    k = (_CAM_D / np.maximum(_CAM_D - P[:, 2], 0.35)) * R
    px, py = cx + P[:, 0] * k, cy - P[:, 1] * k
    out = np.zeros((len(uv), 3), np.float32)
    d2s = []
    for i, (u, v) in enumerate(uv):
        d2 = (px - u) ** 2 + (py - v) ** 2
        d2s.append(d2)
        near = np.flatnonzero(d2 < 9.0)
        j = near[np.argmax(P[near, 2])] if near.size else int(np.argmin(d2))
        out[i] = P[j]
    # A point seen through a gap — the slit between parted lips, a lash line
    # — lands on whatever is behind it: Angelica's mid-lip came back from the
    # inside of her head (z −0.6 against her lips' 0.75) and bent the mouth
    # line into a V. Each feature sits at one depth; a point far behind its
    # group is put back on the surface nearest that depth.
    for grp in (LIPS_UP + LIPS_LO + LIPS_OUT, EYE_R_UP + EYE_R_LO, EYE_L_UP + EYE_L_LO):
        zm = float(np.median(out[grp, 2]))
        for i in grp:
            if out[i, 2] < zm - 0.08:
                near = np.flatnonzero(d2s[i] < 64.0)
                if near.size:
                    out[i] = P[near[np.argmin(np.abs(P[near, 2] - zm))]]
    # Sanity: a real face has its eyes above its mouth and its lips between
    # its mouth corners. Anything else is a detector hallucinating on a mask.
    mouth_y = out[LIPS_UP + LIPS_LO, 1].mean()
    if not (out[EYE_R_UP + EYE_L_UP, 1].mean() > mouth_y + 0.2
            and out[308, 0] - out[78, 0] > 0.1):
        return None
    return out


def load_or_detect(head, key: str | None) -> np.ndarray | None:
    """Cached landmarks for this model (an empty file remembers "no face")."""
    from core.mesh_import import CACHE_DIR
    if not available() or not head.supports_surface:
        return None
    fn = CACHE_DIR / f"{key}.face.npy" if key else None
    if fn is not None and fn.exists():
        a = np.load(fn)
        return a if a.size else None
    try:
        pts = detect(head)
    except Exception as e:
        print(f"[face] landmark detection failed: {e}")
        return None
    if fn is not None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            np.save(fn, pts if pts is not None else np.zeros((0, 3), np.float32))
        except OSError:
            pass
    return pts


# ── the rig ───────────────────────────────────────────────────────────────────

def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def build(pts: np.ndarray, samples: dict, gpu_pos: np.ndarray | None = None) -> dict:
    """Everything the shader and the particles need, from the landmarks."""
    up, lo = pts[LIPS_UP], pts[LIPS_LO]
    seam = 0.5 * (up + lo)
    order = np.argsort(seam[:, 0])
    seam = seam[order]
    xr, xl = float(pts[78, 0]), float(pts[308, 0])
    xc, cw = 0.5 * (xr + xl), 0.5 * (xl - xr)
    # The landmarks' inner-lip line as it is. Snapping it to the mesh's
    # "groove" was tried: the deepest point near the line is as often the
    # underside of the lower lip, and the cut zig-zagged into a W.
    my = float(np.interp(xc, seam[:, 0], seam[:, 1]))
    mz = float(pts[LIPS_OUT, 2].max())
    eyes = []
    P, rgb = samples["pos"], samples.get("rgb")
    for ups, los, iris in ((EYE_R_UP, EYE_R_LO, IRIS_R), (EYE_L_UP, EYE_L_LO, IRIS_L)):
        u_, l_ = pts[ups], pts[los]
        x0, x1 = float(min(u_[:, 0].min(), l_[:, 0].min())), float(max(u_[:, 0].max(), l_[:, 0].max()))
        xs = np.linspace(x0, x1, LID_N)
        uo, lo_ = np.argsort(u_[:, 0]), np.argsort(l_[:, 0])
        up_y = np.interp(xs, u_[uo, 0], u_[uo, 1])
        lo_y = np.interp(xs, l_[lo_, 0], l_[lo_, 1])
        eyes.append({"x0": x0, "x1": x1, "up": up_y.astype(np.float32),
                     # Median, not max: a strand of hair across one eye put
                     # its "front" in the hair and that eye never blinked.
                     "lo": lo_y.astype(np.float32), "z": float(np.median(np.r_[u_[:, 2], l_[:, 2]])),
                     "iris": pts[iris, :2].astype(np.float32),
                     "w": (x1 - x0) * 0.5})
    # The lids' colour: skin just above the upper lid, under the brow.
    skin = np.array([0.75, 0.62, 0.55], np.float32)
    if rgb is not None and float(np.ptp(rgb)) < 1e-3:
        # No texture: the lids are the same clay as the rest (the realistic
        # shader's 0.26 linear, in sRGB) — white "skin" made white blobs.
        skin = np.full(3, 0.26 ** (1 / 2.2), np.float32)
    elif rgb is not None:
        ring = np.zeros(len(P), bool)
        for e in eyes:
            xs = np.linspace(e["x0"], e["x1"], LID_N)
            for x, y in zip(xs[2:-2], e["up"][2:-2]):
                ring |= (np.abs(P[:, 0] - x) < 0.02) & (P[:, 1] > y + 0.02) & (P[:, 1] < y + 0.05) \
                        & (P[:, 2] > e["z"] - 0.12)
        if ring.sum() > 8:
            skin = np.median(rgb[ring], axis=0).astype(np.float32)
    return {"seam": seam.astype(np.float32), "xc": xc, "cw": cw, "mouth_y": my,
            "mouth_z": mz, "chin_y": float(pts[CHIN, 1]),
            "eye_y": float(np.mean([e["up"].mean() * 0.5 + e["lo"].mean() * 0.5 for e in eyes])),
            "eyes": eyes, "skin": skin, "points": pts}


def jaw_weights(q: np.ndarray, rig: dict, zfront: float, pivot_z: float) -> np.ndarray:
    """How much of the jaw's turn each point follows: all of it below the
    lips' line (sharp across the mouth so the lips part cleanly), tied at the
    corners, softening past them so the cheeks stretch, nothing in the neck
    or out at the ears."""
    seam, xc, cw = rig["seam"], rig["xc"], rig["cw"]
    x, y, z = q[:, 0], q[:, 1], q[:, 2]
    s = np.interp(x, seam[:, 0], seam[:, 1])
    ax = np.abs(x - xc)
    d = s - y                                       # > 0 below the line
    tw = 0.004 + 0.6 * np.maximum(0.0, ax - cw)     # sharp in the mouth, soft beyond
    w = _smooth(-0.001, tw, d)
    xn = np.clip(ax / cw, 0.0, 1.0) ** 2
    w *= (1.0 - xn) + xn * _smooth(0.02, 0.14, d)   # corners tied, chin free
    w *= 1.0 - _smooth(1.9 * cw, 3.2 * cw, ax)
    w *= np.clip(0.3 + 0.85 * (z - pivot_z) / max(1e-3, zfront - pivot_z), 0.0, 1.0)
    ch = rig["chin_y"]
    w *= _smooth(ch - 0.30, ch - 0.05, y)
    return w.astype(np.float32)


def augment(gpu: dict, rig: dict, zfront: float, pivot_z: float) -> tuple[dict, np.ndarray]:
    """The model's GPU mesh rigged for speech: each vertex's jaw weight, and
    the triangles that bridge upper and lower lip turned into the inside of
    the mouth.

    Most downloaded heads have their lips welded shut. Deleting the bridge
    left a ragged hole; a mouth bag behind it poked through the lower lip,
    which curves back. Kept and given their own copies of their vertices, the
    bridge triangles stretch exactly across the opening as the jaw drops —
    and the fragment shader paints them as teeth, dark, teeth, from the upper
    lip to the lower (a_cav = (1, 0 at the upper lip … 1 at the lower)). Shut,
    they have no area and the lips look as modelled."""
    pos, idx = gpu["pos"], gpu["idx"].astype(np.int64)
    w = jaw_weights(pos, rig, zfront, pivot_z)
    seam, xc, cw, mz = rig["seam"], rig["xc"], rig["cw"], rig["mouth_z"]
    s = np.interp(pos[:, 0], seam[:, 0], seam[:, 1])
    inmouth = ((np.abs(pos[:, 0] - xc) < cw * 1.02) & (np.abs(pos[:, 1] - s) < 0.12)
               & (pos[:, 2] > mz - 0.25))
    # Which side of the lips' line, not "how much jaw": the weight is tapered
    # at the corners on purpose, and splitting by it ran the cut diagonally
    # down the chin.
    lower = (s - pos[:, 1]) > 0.0
    tl = lower[idx]
    # Any triangle in the mouth that the jaw stretches — across the lips'
    # line, or one hugging it whose corners take different shares of the
    # turn — opens into the mouth; left as lip, those were slivers of lip
    # hanging inside it like fangs.
    wt = w[idx]
    core = ((np.abs(pos[:, 0] - xc) < cw * 0.92) & (np.abs(pos[:, 1] - s) < 0.05))[idx].all(1)
    stretch = ((wt.max(1) - wt.min(1)) > 0.25) & core
    bridge = np.flatnonzero(inmouth[idx].all(1) & ((tl.any(1) & ~tl.all(1)) | stretch))

    n0 = len(pos)
    dup = idx[bridge].ravel()                      # one fresh vertex per corner
    new_ids = n0 + np.arange(len(dup))
    idx = idx.copy()
    idx[bridge] = new_ids.reshape(-1, 3)
    cav = np.zeros((n0 + len(dup), 2), np.float32)
    cav[n0:, 0] = 1.0
    cav[n0:, 1] = lower[dup].astype(np.float32)

    out = dict(gpu)
    out["pos"] = np.vstack([pos, pos[dup]]).astype(np.float32)
    out["nrm"] = np.vstack([gpu["nrm"], gpu["nrm"][dup]]).astype(np.float32)
    out["uv"] = np.vstack([gpu["uv"], gpu["uv"][dup]]).astype(np.float32)
    eye = gpu.get("eye", np.zeros(n0, np.float32))
    out["eye"] = np.concatenate([eye, eye[dup]]).astype(np.float32)
    out["idx"] = idx.astype(np.uint32)
    out["cav"] = cav
    mp = gpu.get("morph_pos")
    if mp is not None and len(mp):
        out["morph_pos"] = np.concatenate([mp, mp[:, dup]], axis=1)
    return out, np.concatenate([w, w[dup]]).astype(np.float32)
