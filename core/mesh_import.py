"""
Bring your own head: turn an OBJ or GLB file into a holographic HUD style.

Drop `assets/avatars/<name>.obj` (or `.glb`) into the project and it appears in
⚙ CUSTOMISE → HUD STYLE as `<name>`, rendered by `core.holo_head.HoloHead` —
the same light, bloom and lip-sync drive as the built-in head.

No dependency: OBJ is parsed directly, and GLB (binary glTF 2.0, what Meshy,
Tripo, Sketchfab and Blender export) is read with the small parser below —
positions and indices of every mesh in the default scene, with node transforms.

Optional sidecar `<name>.json` fine-tunes a model whose proportions the
defaults do not fit:

    {
      "rotate_y": 180,          # degrees; turn the model to face the viewer
      "head_frac": 0.78,        # top fraction of the model that is the head
      "mouth": 0.62,            # mouth height, 0 = crown .. 1 = chin
      "eyes": [[-0.3, 0.05, 0.6], [0.3, 0.05, 0.6]]   # in normalised units
    }

Normalised frame (as `core.avatar_mesh`): +y up, +z towards the viewer, crown
at y = +1, chin at y = -1.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

AVATAR_DIR = Path(__file__).resolve().parent.parent / "assets" / "avatars"
_MAX_EDGES = 3500          # structure lines drawn over the cloud


# ── readers ───────────────────────────────────────────────────────────────────

def _read_obj(path: Path):
    verts, faces = [], []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("v "):
            verts.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(tok.split("/")[0]) for tok in line.split()[1:]]
            idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
            for k in range(1, len(idx) - 1):           # fan-triangulate polygons
                faces.append([idx[0], idx[k], idx[k + 1]])
    return np.array(verts, dtype=np.float64), np.array(faces, dtype=np.int64)


_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16,
         5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    m = np.eye(4)
    if "scale" in node:
        m = m @ np.diag(list(node["scale"]) + [1.0])
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        rm = np.eye(4); rm[:3, :3] = r
        m = rm @ m
    if "translation" in node:
        tm = np.eye(4); tm[:3, 3] = node["translation"]
        m = tm @ m
    return m


def _read_glb(path: Path):
    data = path.read_bytes()
    magic, _ver, _len = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a binary glTF (.glb) file")
    off, gltf, binc = 12, None, b""
    while off < len(data):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            gltf = json.loads(chunk.decode("utf-8"))
        elif ctype.startswith(b"BIN"):
            binc = chunk
        off += 8 + clen
    if gltf is None:
        raise ValueError("glb has no JSON chunk")

    def accessor(i):
        a = gltf["accessors"][i]
        bv = gltf["bufferViews"][a["bufferView"]]
        dt = np.dtype(_COMP[a["componentType"]])
        nc = _NCOMP[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or dt.itemsize * nc
        raw = np.frombuffer(binc, dtype=np.uint8, count=stride * (a["count"] - 1)
                            + dt.itemsize * nc, offset=start)
        rows = np.lib.stride_tricks.as_strided(
            raw, shape=(a["count"], dt.itemsize * nc), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dt).reshape(a["count"], nc)
        return out.astype(np.float64)

    V, F, base = [], [], 0

    def visit(ni, parent):
        node = gltf["nodes"][ni]
        m = parent @ _node_matrix(node)
        nonlocal base
        if "mesh" in node:
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4 or "POSITION" not in prim["attributes"]:
                    continue
                pos = accessor(prim["attributes"]["POSITION"])
                pos = (np.c_[pos, np.ones(len(pos))] @ m.T)[:, :3]
                if "indices" in prim:
                    idx = accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
                else:
                    idx = np.arange(len(pos)).reshape(-1, 3)
                V.append(pos); F.append(idx + base); base += len(pos)
        for c in node.get("children", []):
            visit(c, m)

    scene = gltf["scenes"][gltf.get("scene", 0)]
    for ni in scene["nodes"]:
        visit(ni, np.eye(4))
    if not V:
        raise ValueError("glb contains no triangle meshes")
    return np.vstack(V), np.vstack(F)


# ── normalisation and rigging ─────────────────────────────────────────────────

def _normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.zeros_like(v)
    for k in range(3):
        np.add.at(n, f[:, k], fn)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    # Point them away from the centre of mass: imported meshes come with any
    # winding, and a flipped normal turns the Fresnel rim inside out.
    out = v - v.mean(axis=0)
    flip = (n * out).sum(1) < 0
    if flip.mean() > 0.5:
        n = -n
    return n


def load_avatar(path: str | Path) -> dict:
    """Read, normalise and rig a head model into the mesh dict HoloHead uses."""
    path = Path(path)
    cfg = {}
    side = path.with_suffix(".json")
    if side.exists():
        cfg = json.loads(side.read_text(encoding="utf-8"))
    v, f = (_read_glb(path) if path.suffix.lower() == ".glb" else _read_obj(path))
    if len(f) == 0:
        raise ValueError("no faces")
    # Drop degenerate and unused geometry.
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    used = np.unique(f)
    remap = np.full(len(v), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    v, f = v[used], remap[f]

    ry = np.radians(float(cfg.get("rotate_y", 0.0)))
    if ry:
        c, s = np.cos(ry), np.sin(ry)
        v = v @ np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]).T

    # Crown at +1, chin at -1: the head is taken to be the top `head_frac` of
    # the model (a bust's shoulders hang below), and centred on x/z.
    lo, hi = v[:, 1].min(), v[:, 1].max()
    head_frac = float(cfg.get("head_frac", 0.80))
    chin = hi - (hi - lo) * head_frac
    scale = 2.0 / max(1e-9, hi - chin)
    head = v[:, 1] >= chin
    centre = np.array([v[head, 0].mean(), (hi + chin) / 2.0,
                       (v[head, 2].min() + v[head, 2].max()) / 2.0])
    v = ((v - centre) * scale).astype(np.float32)
    n = _normals(v.astype(np.float64), f).astype(np.float32)

    # Jaw: everything in front and below the mouth line swings on a hinge
    # between the ears — the same rig the built-in head uses, by proportion.
    mouth_y = 1.0 - 2.0 * float(cfg.get("mouth", 0.62))
    zf = v[:, 2]
    jaw = np.clip((mouth_y - v[:, 1]) / max(1e-6, mouth_y + 1.0), 0.0, 1.0) ** 0.8
    jaw *= np.clip(0.3 + 0.85 * (zf / max(1e-6, zf.max())), 0.0, 1.0)
    jaw[v[:, 1] < -1.05] = 0.0
    bottom = v[:, 1].min()
    fade = np.clip((v[:, 1] - bottom) / 0.45, 0.25, 1.0).astype(np.float32)

    e = np.vstack([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    if len(e) > _MAX_EDGES:
        e = e[np.random.default_rng(1).choice(len(e), _MAX_EDGES, replace=False)]

    lm = {}
    if "eyes" in cfg:
        lm["eye_points"] = np.array(cfg["eyes"], dtype=np.float32)

    zeros = np.zeros(len(v), dtype=np.float32)
    return {
        "verts": np.ascontiguousarray(v), "normals": np.ascontiguousarray(n),
        "faces": np.ascontiguousarray(f.astype(np.int32)),
        "edges": np.ascontiguousarray(e.astype(np.int32)),
        "jaw": jaw.astype(np.float32), "brow": zeros, "lips": zeros,
        "lip_centre": np.array([0.0, mouth_y, float(zf.max())], dtype=np.float32),
        "fade": fade, "face_group": np.ones(len(f), dtype=np.float32),
        "landmarks": lm,
        "span": (1.0, float(bottom)),
    }


def discover() -> dict[str, Path]:
    """Model files in assets/avatars, by style key ("model:<name>")."""
    if not AVATAR_DIR.is_dir():
        return {}
    out = {}
    for p in sorted(AVATAR_DIR.iterdir()):
        if p.suffix.lower() in (".obj", ".glb") and not p.name.startswith("_"):
            out[f"model:{p.stem}"] = p
    return out
