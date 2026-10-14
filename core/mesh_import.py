"""
Bring your own head: turn an OBJ or GLB file into a holographic HUD style.

Drop `assets/avatars/<name>.obj` (or `.glb`) into the project and it appears in
⚙ CUSTOMISE → HUD STYLE as `<name>`, rendered by `core.holo_head.HoloHead` —
the same light, bloom and lip-sync drive as the built-in head.

No dependency: OBJ is parsed directly, and GLB (binary glTF 2.0, what Meshy,
Tripo, Sketchfab and Blender export) is read with the small parser below —
every mesh of the default scene with its node transforms, skinned meshes
posed through their skeleton, and each material's base-colour texture.

What the importer does with a model, and why
--------------------------------------------
Downloaded models are made to be looked at in a lit, opaque renderer. Drawn as
additive light straight from their triangles they came out as noise: a
Fortnite skin is a whole body in T-pose (the "head" was its top 80 %, arms and
all), a Tripo scan is 950 k triangles facing sideways (119 ms a frame), and a
character with real hair is 57 % hair cards, whose every layer added light over
the face. So, once per file (then cached in `config/avatar_cache/`):

  * **Head isolated.** A width profile, crown down, finds where the shoulders
    (or a T-pose's arms) jump out and drops everything below them.
  * **Turned to face the viewer** using the eye meshes when the model has them
    (`rotate` in the sidecar always wins).
  * **Surface sampled here, not per frame.** The hologram is a cloud of points
    on the surface; they are sampled on the full-resolution mesh, with their
    normal, rig weights and the *texture's brightness* at that spot — which is
    what brings back eyes, brows, lips and hair partings. Texels the material
    makes transparent (hair-card alpha) get no points at all.

Optional sidecar `<name>.json` fine-tunes a model the defaults do not fit:

    {
      "rotate": [0, 90, 0],     # degrees about x, y, z, applied in that order
      "head_frac": 0.78,        # top fraction of the kept model that is the head
      "crop": 0.42,             # keep only the top fraction of the model
      "mouth": 0.62,            # mouth height, 0 = crown .. 1 = chin
      "eyes": [[-0.3, 0.05, 0.6], [0.3, 0.05, 0.6]],   # or false
      "texture": 1.0            # 0 ignores the textures
    }

(`rotate_y` alone, the old key, still works.) Normalised frame (as
`core.avatar_mesh`): +y up, +z towards the viewer, crown at y = +1, chin at
y = -1.
"""

from __future__ import annotations

import hashlib
import math
import json
import re
import struct
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
AVATAR_DIR = _ROOT / "assets" / "avatars"
CACHE_DIR = _ROOT / "config" / "avatar_cache"
# Bump when the processing below changes, so stale caches are rebuilt.
_VERSION = 16
N_SAMPLES = 48_000         # HoloHead draws up to 44 k on the GPU, 26 k on the CPU
_MAX_EDGES = 3500          # structure lines drawn over the cloud
_EDGE_MAX_FACES = 20_000   # above this, edges are sub-pixel noise — none drawn
_TEX_MAX = 1024            # textures are downscaled to this; points are ~3 px apart
_FLOOR = -1.55            # lowest point kept, in head-half-heights below centre


# ── textures ──────────────────────────────────────────────────────────────────

def _decode_texture(blob: bytes):
    """The image as an RGBA uint8 array (h, w, 4), or None.
    Qt decodes it — PyQt6 is already here and reads PNG/JPEG/WebP off the GUI
    thread without an application object."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QImage
    except Exception:
        return None
    img = QImage.fromData(blob)
    if img.isNull():
        return None
    if max(img.width(), img.height()) > _TEX_MAX:
        img = img.scaled(_TEX_MAX, _TEX_MAX, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(h, bpl)[:, :w * 4].reshape(h, w, 4).copy()


def _sample_tex(tex: np.ndarray, uv: np.ndarray):
    """(rgb n×3, alpha n) in 0..1 at each uv (nearest texel, wrapping)."""
    h, w = tex.shape[:2]
    x = np.minimum((np.mod(uv[:, 0], 1.0) * w).astype(np.int64), w - 1)
    y = np.minimum((np.mod(uv[:, 1], 1.0) * h).astype(np.int64), h - 1)
    t = tex[y, x].astype(np.float32) / 255.0
    return t[:, :3], t[:, 3]


class _Part:
    """One drawable piece of a model: triangles plus what lights them."""

    def __init__(self, name, pos, faces, nrm=None, uv=None, tex=None,
                 factor=(1.0, 1.0, 1.0, 1.0), blend=None):
        self.name = name or ""
        self.pos = np.asarray(pos, np.float64)
        self.faces = np.asarray(faces, np.int64).reshape(-1, 3)
        self.nrm = None if nrm is None else np.asarray(nrm, np.float64)
        self.uv = None if uv is None else np.asarray(uv, np.float64)
        self.tex = tex
        self.factor = tuple(factor)   # RGBA colour factor of the material
        self.morphs: dict[str, np.ndarray] = {}
        # Whether the texture's alpha cuts holes (hair cards, lace). None: decide
        # from the texture itself (OBJ has no alpha mode).
        if blend is None:
            blend = tex is not None and float((tex[..., 3] < 128).mean()) > 0.02
        self.blend = bool(blend)


# ── OBJ ───────────────────────────────────────────────────────────────────────

def _read_mtl(path: Path) -> dict:
    """material name -> (texture, factor) from an OBJ's .mtl, best effort."""
    out, cur = {}, None
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        tok = line.strip().split(None, 1)
        if not tok:
            continue
        if tok[0] == "newmtl" and len(tok) > 1:
            cur = tok[1].strip()
            out[cur] = [None, (1.0, 1.0, 1.0, 1.0)]
        elif cur and tok[0] == "Kd" and len(tok) > 1:
            r, g, b = (float(x) for x in tok[1].split()[:3])
            out[cur][1] = (r, g, b, out[cur][1][3])
        elif cur and tok[0] == "d" and len(tok) > 1:
            out[cur][1] = out[cur][1][:3] + (float(tok[1].split()[0]),)
        elif cur and tok[0] == "map_Kd" and len(tok) > 1:
            tp = path.parent / tok[1].split()[-1]
            if tp.exists():
                out[cur][0] = _decode_texture(tp.read_bytes())
    return out


def _nums(txt: bytes, dtype) -> np.ndarray:
    return np.array(txt.split()).astype(dtype)


def _read_obj(path: Path) -> list[_Part]:
    """Vectorised where it counts: a 130 MB sculpt (820 k vertices, 1.6 M
    faces) took 78 s, 60 of them in np.fromstring, whose text parser is slow
    on integers. Tokens split in bytes and cast by numpy (`_nums`) take ~6 s
    for the whole file — once, before the cache."""
    lines = path.read_bytes().split(b"\n")
    vl, vtl, fl, fmat = [], [], [], []
    mats, cur, mtl = {}, None, {}
    for ln in lines:
        c = ln[:2]
        if c == b"v ":
            vl.append(ln[2:])
        elif c == b"f ":
            fl.append(ln[2:])
            fmat.append(cur)
        elif c == b"vt":
            vtl.append(ln[3:])
        elif ln.startswith(b"usemtl"):
            cur = ln[6:].strip().decode("utf-8", "ignore")
            mats.setdefault(cur, len(mats))
        elif ln.startswith(b"mtllib"):
            mtl.update(_read_mtl(path.parent / ln[6:].strip().decode("utf-8", "ignore")))
    if not vl or not fl:
        raise ValueError("OBJ has no geometry")
    k = len(vl[0].split())
    V = _nums(b" ".join(vl), np.float64)
    V = V.reshape(-1, k)[:, :3] if V.size % k == 0 else np.array(
        [[float(x) for x in s.split()[:3]] for s in vl])
    VT = None
    if vtl:
        kt = len(vtl[0].split())
        VT = _nums(b" ".join(vtl), np.float64)
        VT = VT.reshape(-1, kt)[:, :2] if VT.size % kt == 0 else None

    # Faces, grouped by corner count and token format ("1", "1/2", "1//3",
    # "1/2/3" — one file mixed the first two) so each group parses in one call.
    def shape(s):
        t = s.split()
        if not t:
            return 0
        return len(t) * 4 + (3 if b"//" in t[0] else t[0].count(b"/"))

    key = np.fromiter((shape(s) for s in fl), np.int32, len(fl))
    ncorner, fmt = key // 4, key % 4
    fv, ft, fm = [], [], []
    mat_id = np.array([-1 if m is None else mats[m] for m in fmat], np.int32)
    for kc, kf in {(int(a), int(b)) for a, b in zip(ncorner, fmt)}:
        if kc < 3:
            continue
        sel = np.flatnonzero((ncorner == kc) & (fmt == kf))
        txt = b" ".join(fl[i] for i in sel).replace(b"//", b"/").replace(b"/", b" ")
        arr = _nums(txt, np.int64)
        comps = {0: 1, 1: 2, 2: 3, 3: 2}.get(kf, 1)     # 3 = "a//c": v and vn
        if arr.size != len(sel) * kc * comps:
            raise ValueError("OBJ faces in an unsupported format")
        arr = arr.reshape(len(sel), kc, comps)
        vi = arr[:, :, 0]
        ti = arr[:, :, 1] if kf in (1, 2) else np.zeros_like(vi)   # 0 = no uv
        for j in range(1, kc - 1):                     # fan-triangulate
            fv.append(vi[:, [0, j, j + 1]])
            ft.append(ti[:, [0, j, j + 1]])
            fm.append(mat_id[sel])
    F = np.vstack(fv)
    F = np.where(F > 0, F - 1, len(V) + F)
    FM = np.concatenate(fm)
    FT = None
    if VT is not None:
        FT = np.vstack(ft)
        FT = np.where(FT > 0, FT - 1, np.where(FT < 0, len(VT) + FT, -1))

    names = {v: k for k, v in mats.items()}
    parts = []
    for m in np.unique(FM):
        sel = FM == m
        name = names.get(int(m), "")
        tex, factor = mtl.get(name, (None, (1.0, 1.0, 1.0, 1.0)))
        if FT is not None and tex is not None:
            # A textured OBJ indexes position and UV separately: split into one
            # vertex per (position, uv) pair so both can be interpolated.
            pair = np.stack([F[sel].ravel(), FT[sel].ravel()], axis=1)
            uniq, inv = np.unique(pair, axis=0, return_inverse=True)
            uv = np.where(uniq[:, 1:2] >= 0, VT[np.maximum(uniq[:, 1], 0)], 0.0)
            uv[:, 1] = 1.0 - uv[:, 1]                  # OBJ's v runs bottom-up
            parts.append(_Part(name, V[uniq[:, 0]], inv.reshape(-1, 3),
                               uv=uv, tex=tex, factor=factor))
        else:
            parts.append(_Part(name, V, F[sel], factor=factor))
    return parts


# ── GLB ───────────────────────────────────────────────────────────────────────

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


def _read_glb(path: Path) -> list[_Part]:
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

    def view_bytes(bvi):
        bv = gltf["bufferViews"][bvi]
        s = bv.get("byteOffset", 0)
        return binc[s: s + bv["byteLength"]]

    def accessor(i):
        a = gltf["accessors"][i]
        dt = np.dtype(_COMP[a["componentType"]])
        nc = _NCOMP[a["type"]]
        if "bufferView" not in a:
            return _sparse(a, np.zeros((a["count"], nc)))
        bv = gltf["bufferViews"][a["bufferView"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or dt.itemsize * nc
        raw = np.frombuffer(binc, dtype=np.uint8, count=stride * (a["count"] - 1)
                            + dt.itemsize * nc, offset=start)
        rows = np.lib.stride_tricks.as_strided(
            raw, shape=(a["count"], dt.itemsize * nc), strides=(stride, 1))
        out = np.ascontiguousarray(rows).view(dt).reshape(a["count"], nc)
        out = out.astype(np.float64)
        if a.get("normalized") and dt.kind in "iu":
            out /= float(np.iinfo(dt).max)
        return _sparse(a, out)

    def _sparse(a, out):
        """glTF sparse accessors: a few rows replaced — how exporters store
        morph targets, which touch a small part of the mesh."""
        sp = a.get("sparse")
        if not sp:
            return out
        n = sp["count"]
        ii, vv = sp["indices"], sp["values"]
        idt = np.dtype(_COMP[ii["componentType"]])
        bvi = gltf["bufferViews"][ii["bufferView"]]
        ind = np.frombuffer(binc, idt, n, bvi.get("byteOffset", 0) + ii.get("byteOffset", 0))
        dt = np.dtype(_COMP[a["componentType"]])
        nc = _NCOMP[a["type"]]
        bvv = gltf["bufferViews"][vv["bufferView"]]
        val = np.frombuffer(binc, dt, n * nc, bvv.get("byteOffset", 0) + vv.get("byteOffset", 0))
        out = out.copy()
        out[ind.astype(np.int64)] = val.reshape(n, nc).astype(np.float64)
        return out

    # World matrix of every node (joints included — skinning needs them).
    world: dict[int, np.ndarray] = {}

    def walk(ni, parent):
        world[ni] = parent @ _node_matrix(gltf["nodes"][ni])
        for c in gltf["nodes"][ni].get("children", []):
            walk(c, world[ni])

    scene = gltf["scenes"][gltf.get("scene", 0)]
    for ni in scene["nodes"]:
        walk(ni, np.eye(4))

    tex_cache: dict[int, object] = {}

    def texture(info):
        if not info or "index" not in info:
            return None
        ti = info["index"]
        if ti not in tex_cache:
            tex_cache[ti] = None
            src = gltf["textures"][ti].get("source")
            if src is None:          # e.g. only an EXT_texture_webp source
                for ext in gltf["textures"][ti].get("extensions", {}).values():
                    src = ext.get("source", src)
            img = gltf.get("images", [])[src] if src is not None else {}
            if "bufferView" in img:
                tex_cache[ti] = _decode_texture(bytes(view_bytes(img["bufferView"])))
        return tex_cache[ti]

    def material(mi):
        """(texture, texCoord set, RGBA factor)."""
        if mi is None:
            return None, 0, (1.0, 1.0, 1.0, 1.0), False
        m = gltf["materials"][mi]
        pbr = m.get("pbrMetallicRoughness", {})
        sg = m.get("extensions", {}).get("KHR_materials_pbrSpecularGlossiness")
        if sg:
            info, fac = sg.get("diffuseTexture"), sg.get("diffuseFactor", [1, 1, 1, 1])
        else:
            info, fac = pbr.get("baseColorTexture"), pbr.get("baseColorFactor", [1, 1, 1, 1])
        blend = m.get("alphaMode", "OPAQUE") != "OPAQUE"
        alpha = fac[3] if blend else 1.0
        return texture(info), (info or {}).get("texCoord", 0), (*fac[:3], alpha), blend

    parts = []
    for ni, wm in world.items():
        node = gltf["nodes"][ni]
        if "mesh" not in node:
            continue
        mesh = gltf["meshes"][node["mesh"]]
        skin = gltf["skins"][node["skin"]] if "skin" in node else None
        for prim in mesh["primitives"]:
            at = prim["attributes"]
            if prim.get("mode", 4) != 4 or "POSITION" not in at:
                continue
            pos = accessor(at["POSITION"])
            nrm = accessor(at["NORMAL"]) if "NORMAL" in at else None
            # Morph targets (blend shapes): deltas named by the mesh's
            # extras.targetNames — jawOpen, eyeBlinkLeft, viseme_aa… Skinning
            # and node transforms are linear, so the deltas go through the
            # same 3×3 as the vertices.
            names = (mesh.get("extras", {}).get("targetNames")
                     or prim.get("extras", {}).get("targetNames") or [])
            morphs = {}
            for ti, tg in enumerate(prim.get("targets", [])):
                if "POSITION" in tg:
                    nm = names[ti] if ti < len(names) else f"morph_{ti}"
                    morphs[nm] = accessor(tg["POSITION"])
            lin = None
            if skin is not None and "JOINTS_0" in at and "WEIGHTS_0" in at:
                # Skinned: the node's own transform is ignored (glTF spec); each
                # vertex follows its joints. Posing it here puts a rigged
                # character in the stance its file shows, not its bind space.
                ibm = (accessor(skin["inverseBindMatrices"]).reshape(-1, 4, 4)
                       .transpose(0, 2, 1) if "inverseBindMatrices" in skin
                       else np.tile(np.eye(4), (len(skin["joints"]), 1, 1)))
                jm = np.stack([world.get(j, np.eye(4)) for j in skin["joints"]]) @ ibm
                J = accessor(at["JOINTS_0"]).astype(np.int64)
                W = accessor(at["WEIGHTS_0"])
                W /= np.maximum(W.sum(1, keepdims=True), 1e-9)
                M = np.einsum("nk,nkij->nij", W, jm[J])
                pos = np.einsum("nij,nj->ni", M[:, :3, :3], pos) + M[:, :3, 3]
                lin = lambda d, M=M: np.einsum("nij,nj->ni", M[:, :3, :3], d)   # noqa: E731
                if nrm is not None:
                    nrm = np.einsum("nij,nj->ni", M[:, :3, :3], nrm)
            else:
                pos = pos @ wm[:3, :3].T + wm[:3, 3]
                lin = lambda d, m3=wm[:3, :3]: d @ m3.T   # noqa: E731
                if nrm is not None:
                    nrm = nrm @ np.linalg.inv(wm[:3, :3])
            morphs = {k: lin(d) for k, d in morphs.items() if np.abs(d).max() > 0}
            idx = (accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
                   if "indices" in prim else np.arange(len(pos)).reshape(-1, 3))
            tex, tc, factor, blend = material(prim.get("material"))
            uv = accessor(at[f"TEXCOORD_{tc}"]) if f"TEXCOORD_{tc}" in at else None
            mname = (gltf["materials"][prim["material"]].get("name", "")
                     if "material" in prim else "")
            parts.append(_Part(f"{mesh.get('name', '')} {node.get('name', '')} {mname}",
                               pos, idx, nrm, uv, tex if uv is not None else None, factor,
                               blend))
            parts[-1].morphs = morphs
    if not parts:
        raise ValueError("glb contains no triangle meshes")
    return parts


# ── geometry helpers ──────────────────────────────────────────────────────────

def _rot_xyz(deg) -> np.ndarray:
    rx, ry, rz = (np.radians(float(a)) for a in deg)
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Z @ Y @ X


def _vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    # Welded first: exporters split vertices along UV seams, and unwelded the
    # seams would light as creases.
    q = np.round(v / max(1e-9, float(np.ptp(v, axis=0).max())) * 2e5).astype(np.int64)
    _, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.ravel()
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    acc = np.zeros((inv.max() + 1, 3))
    for k in range(3):
        np.add.at(acc, inv[f[:, k]], fn)
    n = acc[inv]
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    # Imported meshes come with any winding, and a flipped normal turns the
    # Fresnel rim inside out: point the majority away from the centre.
    if ((v - v.mean(axis=0)) * n).sum(1).mean() < 0:
        n = -n
    return n


def _area_samples(v, f, n, weight=None, seed=3):
    a, b, c = v[f[:, 0]], v[f[:, 1]], v[f[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if weight is not None:
        area = area * weight
    rng = np.random.default_rng(seed)
    tri = rng.choice(len(f), size=n, p=area / area.sum())
    u, w = rng.random(n), rng.random(n)
    flip = u + w > 1.0
    u[flip], w[flip] = 1.0 - u[flip], 1.0 - w[flip]
    return tri, np.stack([1.0 - u - w, u, w], axis=1)


def _width_profile(pts: np.ndarray, top: float, bot: float, bins: int):
    """Horizontal width (x, 2nd–98th percentile) of each slice, crown down."""
    edges = np.linspace(top, bot, bins + 1)
    w = np.full(bins, np.nan)
    for i in range(bins):
        s = pts[(pts[:, 1] <= edges[i]) & (pts[:, 1] > edges[i + 1]), 0]
        if s.size >= 25:
            lo, hi = np.percentile(s, [2, 98])
            w[i] = hi - lo
    return edges, w


def _find_cut(pts: np.ndarray):
    """Where the head ends and the body begins, or None for a head-only model.

    Walking down from the crown, the width grows smoothly over the skull; the
    shoulders — or a T-pose's arms — are a jump to well over 1.7× anything
    above. Shuri's full body (1.67 m) cuts at 1.41 m this way."""
    top, bot = pts[:, 1].max(), pts[:, 1].min()
    edges, w = _width_profile(pts, top, bot, 80)
    run = 0.0
    for i, wi in enumerate(w):
        if np.isnan(wi):
            continue
        if run > 0 and (top - edges[i]) > 0.08 * (top - bot) and wi > 1.7 * run:
            return edges[i] + 0.04 * (top - edges[i])
        run = max(run, wi)
    return None


def _find_neck(pts: np.ndarray):
    """Neck height in the kept part: the narrowest slice in its lower half,
    if it is clearly narrower than the skull (long hair hides it — then None)."""
    top, bot = pts[:, 1].max(), pts[:, 1].min()
    edges, w = _width_profile(pts, top, bot, 40)
    if np.all(np.isnan(w)):
        return None
    skull = np.nanmax(w[:24])
    low = np.arange(18, 40)
    low = low[~np.isnan(w[low])]
    if not low.size:
        return None
    i = low[np.argmin(w[low])]
    return 0.5 * (edges[i] + edges[i + 1]) if w[i] < 0.72 * skull else None


_EYE = re.compile(r"(?<![a-z])eye(?!lash|brow|lid|_?spec|_?reflect)", re.I)


# ── the pipeline ──────────────────────────────────────────────────────────────

def _process(parts: list[_Part], cfg: dict) -> dict:
    # Drop invisible materials and degenerate faces; concatenate.
    keep = [p for p in parts if p.factor[3] > 0.05 and len(p.faces)]
    if not keep:
        raise ValueError("no visible geometry")
    V, F, N, UV, FP = [], [], [], [], []
    mstart = []
    base = 0
    for k, p in enumerate(keep):
        f = p.faces
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        V.append(p.pos)
        mstart.append(base)
        N.append(p.nrm if p.nrm is not None else np.full_like(p.pos, np.nan))
        UV.append(p.uv if p.uv is not None else np.zeros((len(p.pos), 2)))
        F.append(f + base)
        FP.append(np.full(len(f), k, np.int32))
        base += len(p.pos)
    V, N, UV = np.vstack(V), np.vstack(N), np.vstack(UV)
    names = sorted({k for p in keep for k in p.morphs})
    D = {}
    for k in names:                               # one full-size delta per shape
        d = np.zeros_like(V)
        for p, st in zip(keep, mstart):
            if k in p.morphs:
                d[st:st + len(p.pos)] = p.morphs[k]
        D[k] = d
    F, FP = np.vstack(F), np.concatenate(FP)

    # ── orientation ──
    if "rotate" in cfg:
        R = _rot_xyz(cfg["rotate"])
    elif "rotate_y" in cfg:
        R = _rot_xyz([0, cfg["rotate_y"], 0])
    else:
        R = np.eye(3)
    V = V @ R.T
    D = {k: d @ R.T for k, d in D.items()}
    N = N @ R.T

    # ── isolate the head ──
    # Area-weighted points, so the profile measures surface, not vertex density.
    tri, bary = _area_samples(V, F, 60_000, seed=11)
    probe = np.einsum("nk,nkd->nd", bary, V[F[tri]])
    if "crop" in cfg:
        top, bot = probe[:, 1].max(), probe[:, 1].min()
        cut = top - (top - bot) * float(cfg["crop"])
    else:
        cut = _find_cut(probe)
    if cut is not None:
        F_keep = V[F, 1].max(axis=1) >= cut
        F, FP = F[F_keep], FP[F_keep]
        probe = probe[probe[:, 1] >= cut]

    # ── face the viewer: the eyes are in front ──
    eye_parts = [k for k, p in enumerate(keep) if _EYE.search(p.name)]
    eye_v = np.unique(F[np.isin(FP, eye_parts)]) if eye_parts else np.array([], int)
    if "rotate" not in cfg and "rotate_y" not in cfg:
        top, bot = probe[:, 1].max(), probe[:, 1].min()
        if eye_v.size:
            d = V[eye_v][:, [0, 2]].mean(0) - probe[:, [0, 2]].mean(0)
            snap = False
        else:
            # No eye meshes (a scan is one surface): the face is on the side
            # the head overhangs its neck — the jaw is in front of the neck,
            # the skull mostly above it. Snapped to a quarter turn, since a
            # model is either authored facing an axis or this guess is wrong.
            h = top - bot
            skull = probe[probe[:, 1] > top - 0.6 * h]
            neck = probe[probe[:, 1] < bot + 0.12 * h]
            c = 0.5 * (skull[:, [0, 2]].min(0) + skull[:, [0, 2]].max(0))
            d = c - neck[:, [0, 2]].mean(0) if len(neck) > 50 else np.zeros(2)
            if np.hypot(*d) < 0.04 * h:
                d = np.zeros(2)
            snap = True
        if np.hypot(*d) > 1e-9:
            yaw = np.degrees(np.arctan2(d[0], d[1]))     # rotate d onto +z
            if snap:
                yaw = 90.0 * round(yaw / 90.0)
            Ry = _rot_xyz([0, -yaw, 0])
            V, N, probe = V @ Ry.T, N @ Ry.T, probe @ Ry.T
            D = {k: d @ Ry.T for k, d in D.items()}

    # ── normalise: crown +1, chin -1 ──
    hi, lo = probe[:, 1].max(), probe[:, 1].min()
    if "head_frac" in cfg:
        chin = hi - (hi - lo) * float(cfg["head_frac"])
    else:
        neck = _find_neck(probe)
        # The chin sits about an eighth of the head above the neck's waist.
        chin = (neck + 0.12 * (hi - neck)) if neck is not None else hi - 0.80 * (hi - lo)
    scale = 2.0 / max(1e-9, hi - chin)
    head = probe[probe[:, 1] >= chin]
    centre = np.array([np.median(head[:, 0]), (hi + chin) / 2.0,
                       0.5 * (head[:, 2].min() + head[:, 2].max())])
    V = (V - centre) * scale
    D = {k: d * scale for k, d in D.items()}
    # Keep only a collar under the chin. The HUD sizes the head by its whole
    # span, and a stylised bust's shoulders (1.4× its skull — too narrow for
    # the cut above) hung 1.9 units below the chin and shrank the face by 40 %.
    F_keep = V[F, 1].max(axis=1) >= _FLOOR
    F, FP = F[F_keep], FP[F_keep]

    # Normals: the file's own where it has them (authored, hair cards
    # included), computed for the rest.
    miss = np.isnan(N[:, 0])
    if miss.any():
        N[miss] = _vertex_normals(V, F)[miss]
    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)

    # ── the surface samples ──
    # The face front gets three times the points of the back of the skull,
    # as in the built-in head.
    cz = V[F].mean(axis=1)[:, 2]
    zr = max(1e-6, float(np.percentile(V[F[:, 0], 2], 98)))
    front = 1.0 + 2.0 * np.clip(cz / (0.6 * zr), 0.0, 1.0)
    tri, bary = _area_samples(V, F, int(N_SAMPLES * 1.6), weight=front, seed=3)
    tv = F[tri]
    P = np.einsum("nk,nkd->nd", bary, V[tv])
    Nn = np.einsum("nk,nkd->nd", bary, N[tv])
    Nn /= np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-9)
    uv = np.einsum("nk,nkd->nd", bary, UV[tv])
    rgb = np.ones((len(P), 3))
    alpha = np.ones(len(P))
    part = FP[tri]
    use_tex = float(cfg.get("texture", 1.0)) > 0
    for k, p in enumerate(keep):
        s_ = part == k
        if not s_.any():
            continue
        rgb[s_] = p.factor[:3]
        if use_tex and p.tex is not None:
            tc, ta = _sample_tex(p.tex, uv[s_])
            rgb[s_] *= tc
            if p.blend:
                alpha[s_] *= ta
    # Texels the material makes see-through (hair-card alpha, lace) get no
    # light at all: drawn, they were the solid sheets around Angelica's face.
    ok = (alpha > 0.35) & (P[:, 1] >= _FLOOR)
    P, Nn, rgb = P[ok][:N_SAMPLES], Nn[ok][:N_SAMPLES], rgb[ok][:N_SAMPLES]
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722])
    if not use_tex or np.ptp(lum) < 1e-3:
        alb = np.ones(len(P))
    else:
        # Brightness relative to the model's own range, lifted (gamma 0.6):
        # dark hair should read as dimmer light, not as a hole.
        lo_, hi_ = np.percentile(lum, [2, 97])
        alb = np.clip((lum - lo_) / max(1e-6, hi_ - lo_), 0.0, 1.0) ** 0.6

    bottom = float(P[:, 1].min())

    def fade_of(q):
        return np.clip((q[:, 1] - bottom) / 0.45, 0.25, 1.0)

    # ── structure lines: only where a low-poly mesh has edges worth seeing ──
    if len(F) <= _EDGE_MAX_FACES:
        e = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
        e = np.unique(np.sort(e, axis=1), axis=0)
        if len(e) > _MAX_EDGES:
            e = e[np.random.default_rng(1).choice(len(e), _MAX_EDGES, replace=False)]
    else:
        e = np.zeros((0, 2), np.int64)
    used, inv = np.unique(e, return_inverse=True)
    lv = V[used] if used.size else np.zeros((1, 3))
    ln = N[used] if used.size else np.array([[0.0, 0.0, 1.0]])
    e = inv.reshape(-1, 2)

    # ── anatomy: where this head keeps its eyes and mouth ──
    eye_pts = None
    if isinstance(cfg.get("eyes"), list):
        eye_pts = np.array(cfg["eyes"], dtype=np.float64)
    elif eye_v.size:
        ev = V[eye_v]
        sides = [ev[ev[:, 0] < 0], ev[ev[:, 0] >= 0]]
        if all(len(q) > 3 for q in sides):
            eye_pts = np.array([[q[:, 0].mean(), q[:, 1].mean(), q[:, 2].max()]
                                for q in sides])
    anatomy = _anatomy(P, lum if use_tex and np.ptp(lum) > 1e-3 else None, eye_pts,
                       rgb if use_tex else None)
    if "mouth" in cfg:
        anatomy["mouth_y"] = 1.0 - 2.0 * float(cfg["mouth"])
    # Skin just under the eyes: the colour a closing lid is drawn in.
    ring = ((np.abs(np.abs(P[:, 0]) - anatomy["eye_x"]) < 0.08)
            & (P[:, 1] < anatomy["eye_y"] - 0.07) & (P[:, 1] > anatomy["eye_y"] - 0.16)
            & (P[:, 2] > anatomy["eye_z"] - 0.15))
    skin = np.median(rgb[ring], axis=0) if ring.sum() > 10 else np.array([0.75, 0.62, 0.55])
    anatomy.update(lid_r=float(skin[0]), lid_g=float(skin[1]), lid_b=float(skin[2]))
    lm = {}
    if cfg.get("eyes") is not False:
        lm["eye_points"] = np.array(
            [[-anatomy["eye_x"], anatomy["eye_y"], anatomy["eye_z"]],
             [anatomy["eye_x"], anatomy["eye_y"], anatomy["eye_z"]]], dtype=np.float32)

    # ── the whole surface, for the GPU renderer (core.holo_surface) ──
    gpu = _gpu_mesh(V, N, UV, F, FP, keep, use_tex, eye_parts, D)
    er = gpu["eye_r"][gpu["eye_r"] > 0]
    if er.size:
        # The lids' opening, from the eyeball itself: about its width and a
        # bit over half its height show between the lids of an open eye.
        anatomy["eye_w"] = float(0.95 * er.mean())
        anatomy["eye_h"] = float(0.55 * er.mean())

    f32 = lambda a: np.ascontiguousarray(a, dtype=np.float32)   # noqa: E731
    zeros = lambda n: np.zeros(n, np.float32)                     # noqa: E731
    return {
        "verts": f32(lv), "normals": f32(ln),
        "faces": np.zeros((0, 3), np.int32),
        "edges": np.ascontiguousarray(e.astype(np.int32)),
        "jaw": zeros(len(lv)), "brow": zeros(len(lv)), "lips": zeros(len(lv)),
        "lip_centre": np.array([0.0, anatomy["mouth_y"], anatomy["mouth_z"]], dtype=np.float32),
        "fade": f32(fade_of(lv)), "face_group": zeros(0),
        "landmarks": lm,
        "span": (1.0, bottom),
        "anatomy": anatomy,
        "samples": {
            "pos": f32(P), "nrm": f32(Nn), "alb": f32(alb), "rgb": f32(rgb),
            "jaw": zeros(len(P)), "fade": f32(fade_of(P)),
            "brow": zeros(len(P)), "lips": zeros(len(P)),
        },
        "gpu": gpu,
    }


# Built-in head proportions (core.avatar_mesh, measured): with d = nose tip
# to chin, the eye line sits 0.35 d above the nose tip and the mouth 0.42 d
# below it; the eyes are 0.204 of the face's width either side of centre and
# 0.355 behind the nose tip; the jaw hinges 37 % of the head's depth in from
# the back.
_R_EYE_Y, _R_MOUTH_Y, _R_EYE_X, _R_EYE_Z, _R_PIVOT = 0.35, 0.42, 0.204, 0.355, 0.374


def _anatomy(P: np.ndarray, lum, eye_pts, rgb=None) -> dict:
    """Eyes, mouth, chin and jaw hinge of a normalised head.

    Geometry finds the nose (the point that stands out furthest from the
    cheeks beside it) and the chin (where the front profile ends); the
    built-in head's proportions place eyes and mouth from those. A texture,
    where there is one, then moves each to the darkest spot near its guess —
    pupils, lashes and the line between the lips are dark on nearly every
    face. Everything here is only a first guess: HOLOGRAMA's sliders and
    markers calibrate it per model."""
    col = P[(np.abs(P[:, 0]) < 0.06)]
    # Nose: not simply the most forward point — a fringe hanging over the
    # face is further forward than HP_Portrait's nose. A nose stands out from
    # the cheeks either side of it; hair across the face does not.
    # Compared with the cheeks either side (|x| 0.17–0.27), and only among
    # rows near the front of the face — without that, hair behind a Tripo
    # head's jaw made its chin "stand out" more than its nose. Measured on six
    # downloaded heads against hand-placed noses: this variant was the only
    # one within 0.08 on all of them.
    zc_all = float(np.percentile(col[:, 2], 99.5)) if len(col) > 20 else float(P[:, 2].max())
    best, ny, nz = -1e9, -0.2, zc_all
    for y in np.arange(-0.9, 0.36, 0.04):
        c = col[(col[:, 1] > y - 0.04) & (col[:, 1] <= y)]
        side = P[(np.abs(P[:, 0]) > 0.17) & (np.abs(P[:, 0]) < 0.27)
                 & (P[:, 1] > y - 0.04) & (P[:, 1] <= y)]
        if len(c) < 3 or len(side) < 6:
            continue
        zc = float(c[:, 2].max())
        if zc < zc_all - 0.15:
            continue
        prot = zc - float(np.percentile(side[:, 2], 90))
        if prot > best:
            best, ny, nz = prot, float(y - 0.02), zc
    if eye_pts is not None and ny > float(eye_pts[:, 1].mean()) - 0.1:
        # Eye meshes are certain; a nose above them is not a nose.
        ny = float(eye_pts[:, 1].mean()) - 0.28
    # Chin: walk down the middle of the face from the nose until the profile
    # falls back by half the nose's depth — that is the neck. Taking the
    # lowest forward point instead found busts' chests, 0.5 below the chin.
    chin = ny - 0.8
    y = ny - 0.35
    while y > -1.5:
        sl = col[(col[:, 1] <= y) & (col[:, 1] > y - 0.04)]
        if len(sl) and sl[:, 2].max() < nz - 0.5:
            chin = y + 0.02
            break
        y -= 0.04
    chin = float(np.clip(chin, -1.45, ny - 0.4))
    d = ny - chin
    ey = ny + _R_EYE_Y * d
    my = ny - _R_MOUTH_Y * d
    # Face width at the eyes, front surface only: hair at the sides sits
    # further back and made the Tripo head's eyes land on its temples.
    row = P[(np.abs(P[:, 1] - ey) < 0.06) & (P[:, 2] > nz - 0.65)]
    width = float(np.ptp(row[:, 0])) if len(row) > 20 else 1.4
    ex, ez = _R_EYE_X * width, nz - _R_EYE_Z * (width / 1.387)

    if lum is not None:
        def darkest(sel, key, guess, pull):
            """Darkest cell, nudged toward the anatomical guess: a dark
            shadow under the chin should not beat a mouth right where a mouth
            belongs."""
            q, l_ = P[sel], lum[sel]
            if len(q) < 30:
                return None
            k = np.round(q[:, key] / 0.04).astype(np.int64)
            ks, inv, cnt = np.unique(k, axis=0, return_inverse=True, return_counts=True)
            m = np.bincount(inv.ravel(), weights=l_) / cnt
            m[cnt < 4] = np.inf
            med = float(np.median(l_))
            score = m / max(1e-3, med) + pull * np.abs(ks * 0.04 - guess).sum(axis=1) / max(0.1, d)
            i = int(np.argmin(score))
            if not np.isfinite(m[i]) or m[i] > 0.75 * med:
                return None                       # nothing clearly darker
            return ks[i] * 0.04
        # Mouth: the darkest row of the middle of the lower face.
        sel = ((np.abs(P[:, 0]) < 0.10) & (P[:, 1] < ny - 0.2 * d)
               & (P[:, 1] > ny - 0.65 * d) & (P[:, 2] > nz - 0.5))
        r = darkest(sel, [1], np.array([my]), 1.5)
        if r is not None:
            my = float(r[0])

        # Eyes: the darkest cell on each side around the guessed eye.
        hits = []
        for sgn in (-1, 1):
            sel = ((sgn * P[:, 0] > 0.35 * ex) & (sgn * P[:, 0] < 1.6 * ex)
                   & (np.abs(P[:, 1] - ey) < 0.16) & (P[:, 2] > nz - 0.6))
            hits.append(darkest(sel, [0, 1], np.array([sgn * ex, ey]), 0.6))
        if all(h is not None for h in hits):
            ex = 0.5 * (abs(hits[0][0]) + abs(hits[1][0]))
            ey = 0.5 * (hits[0][1] + hits[1][1])
    seam = _lip_seam(P, ny, chin)
    if seam is not None:
        my = seam
    if eye_pts is not None:                       # eye meshes beat any guess
        ex = float(np.abs(eye_pts[:, 0]).mean())
        ey = float(eye_pts[:, 1].mean())
        ez = float(eye_pts[:, 2].mean())
    else:
        near = P[(np.abs(np.abs(P[:, 0]) - ex) < 0.06) & (np.abs(P[:, 1] - ey) < 0.06)]
        if len(near) > 5:
            ez = float(np.percentile(near[:, 2], 90))
    near = P[(np.abs(P[:, 1] - my) < 0.08) & (np.abs(P[:, 0]) < 0.15)]
    mz = float(np.percentile(near[:, 2], 95)) if len(near) > 10 else nz - 0.25
    row = P[np.abs(P[:, 1] - ey) < 0.08]
    zb, zf = (float(row[:, 2].min()), float(row[:, 2].max())) if len(row) > 20 else (-0.85, 0.52)
    return {"eye_x": float(ex), "eye_y": float(ey), "eye_z": float(ez),
            # Painted eyes: the opening of the built-in head's eye, a little
            # generous — a lid that falls short of the eye shows it through.
            "eye_w": float(0.075 * width), "eye_h": float(0.040 * width),
            # Lips reach 0.78 of the way to the eyes' centres either side
            # (the built-in head: 0.22 against 0.283).
            "mouth_y": float(my), "mouth_z": mz, "mouth_w": float(0.78 * ex),
            "chin_y": chin, "nose_y": ny, "nose_z": nz,
            "pivot_y": float(ey - 0.02), "pivot_z": float(zb + _R_PIVOT * (zf - zb))}


def _sphere_centre(q: np.ndarray) -> np.ndarray:
    """Least-squares sphere centre of an eyeball's vertices. The centroid is
    not it: many eyes model only the front cap (iris and cornea), whose
    centroid sits right at the pupil — turning about that does nothing."""
    A = np.c_[2.0 * q, np.ones(len(q))]
    b = (q * q).sum(1)
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        c = sol[:3]
        r = math.sqrt(max(1e-9, sol[3] + c @ c))
        if 0.02 < r < 0.35:
            return c, r
    except Exception:
        pass
    return q.mean(0) - np.array([0.0, 0.0, 0.06]), 0.08


def _lip_seam(P, ny, chin):
    """The line between the lips, from the shape of the face's middle.

    Colour failed both ways — redness found Angelica's nostrils (her lips are
    paler than her skin), the darkest row found the shadow under her lower
    lip. Shape does not depend on make-up: down the centre line the lips meet
    in a crease, a dip in the front profile. Of the dips between nose tip and
    chin, the one nearest where the built-in head keeps its mouth (0.43 of
    the way from nose tip to chin) wins. Measured on Angelica: −0.49, where a
    render sweep put her seam at −0.48 to −0.50."""
    col = P[np.abs(P[:, 0]) < 0.05]
    near = col[np.abs(col[:, 1] - ny) < 0.15]
    if len(near) < 5:
        return None
    tip = float(near[np.argmax(near[:, 2]), 1])
    guess = tip - 0.43 * (tip - chin)
    ys = np.arange(tip - 0.06, chin + 0.08, -0.015)
    z = np.full(len(ys), np.nan)
    for i, y in enumerate(ys):
        sl = col[(col[:, 1] <= y + 0.0075) & (col[:, 1] > y - 0.0075)]
        if len(sl):
            z[i] = sl[:, 2].max()
    ok = ~np.isnan(z)
    if ok.sum() < 6:
        return guess
    z = np.interp(np.arange(len(z)), np.flatnonzero(ok), z[ok])
    z = np.convolve(np.pad(z, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    dips = [i for i in range(2, len(z) - 2)
            if z[i] < z[i - 1] and z[i] <= z[i + 1] and z[i] < z[i - 2] and z[i] <= z[i + 2]]
    if not dips:
        return guess
    best = min(dips, key=lambda i: abs(ys[i] - guess))
    return float(ys[best]) if abs(ys[best] - guess) < 0.25 * (tip - chin) else guess


def _gpu_mesh(V, N, UV, F, FP, parts, use_tex, eye_parts=(), D=None) -> dict:
    """Compact vertex buffers and per-part draw ranges (faces grouped by part,
    one texture per part)."""
    order = np.argsort(FP, kind="stable")
    F, FP = F[order], FP[order]
    used, inv = np.unique(F, return_inverse=True)
    idx = inv.reshape(-1, 3).astype(np.uint32)
    texs, ranges = [], []
    for k, p in enumerate(parts):
        sel = np.flatnonzero(FP == k)
        if not sel.size:
            continue
        ti = -1
        if use_tex and p.tex is not None:
            for j, t in enumerate(texs):
                if t is p.tex:
                    ti = j
            if ti < 0:
                texs.append(p.tex)
                ti = len(texs) - 1
        ranges.append([int(sel[0]) * 3, int(sel.size) * 3, ti, int(p.blend),
                       *[float(c) for c in p.factor[:3]]])
    # Eyeballs: which vertices are an eye (1 left, 2 right) and the centre
    # each one turns about, so the shader can move the eyes with the gaze.
    pos = V[used]
    eye = np.zeros(len(used), np.float32)
    centres = np.zeros((2, 3), np.float32)
    radii = np.zeros(2, np.float32)
    if len(eye_parts):
        ev = np.unique(inv.reshape(-1, 3)[np.isin(FP, eye_parts)])
        for side, sel in ((1, pos[ev, 0] < 0), (2, pos[ev, 0] >= 0)):
            ids = ev[sel]
            if len(ids) > 8:
                eye[ids] = side
                centres[side - 1], radii[side - 1] = _sphere_centre(pos[ids])
    return {"pos": np.ascontiguousarray(pos, np.float32),
            "eye": eye, "eye_c": centres, "eye_r": radii,
            "nrm": np.ascontiguousarray(N[used], np.float32),
            "uv": np.ascontiguousarray(UV[used], np.float32),
            "idx": np.ascontiguousarray(idx),
            "ranges": np.array(ranges, np.float64).reshape(-1, 7),
            "textures": texs,
            # Blend shapes, compacted like the vertices (core.blendshapes).
            "morph_names": np.array(sorted(D or {}), dtype=np.str_),
            "morph_pos": (np.stack([D[k][used] for k in sorted(D)]).astype(np.float32)
                          if D else np.zeros((0, len(used), 3), np.float32))}


# ── cache ─────────────────────────────────────────────────────────────────────

def _cache_path(path: Path, cfg_text: str) -> Path:
    st = path.stat()
    key = f"{_VERSION}|{path.name}|{st.st_size}|{st.st_mtime_ns}|{cfg_text}"
    return CACHE_DIR / f"{path.stem}-{hashlib.sha1(key.encode()).hexdigest()[:12]}.npz"


def _save(fn: Path, mesh: dict) -> None:
    flat = {}
    for k, v in mesh.items():
        if k == "samples":
            flat.update({f"s_{sk}": sv for sk, sv in v.items()})
        elif k == "landmarks":
            flat.update({f"lm_{lk}": lv for lk, lv in v.items()})
        elif k == "anatomy":
            flat.update({f"an_{ak}": np.float64(av) for ak, av in v.items()})
        elif k == "gpu":
            for gk, gv in v.items():
                if gk == "textures":
                    flat.update({f"gt_{i}": t for i, t in enumerate(gv)})
                else:
                    flat[f"g_{gk}"] = gv
        else:
            flat[k] = np.asarray(v)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for old in CACHE_DIR.glob(f"{fn.name.rsplit('-', 1)[0]}-*.npz"):
        old.unlink(missing_ok=True)
    tmp = fn.with_suffix(".tmp.npz")
    np.savez(tmp, **flat)
    tmp.replace(fn)


def _load(fn: Path) -> dict:
    with np.load(fn) as z:
        out, s, lm, an, g, tex = {}, {}, {}, {}, {}, {}
        for k in z.files:
            if k.startswith("s_"):
                s[k[2:]] = z[k]
            elif k.startswith("lm_"):
                lm[k[3:]] = z[k]
            elif k.startswith("an_"):
                an[k[3:]] = float(z[k])
            elif k.startswith("gt_"):
                tex[int(k[3:])] = z[k]
            elif k.startswith("g_"):
                g[k[2:]] = z[k]
            else:
                out[k] = z[k]
    g["textures"] = [tex[i] for i in sorted(tex)]
    out.update(samples=s, landmarks=lm, anatomy=an, gpu=g)
    out["span"] = tuple(float(x) for x in out["span"])
    return out


def load_avatar(path: str | Path) -> dict:
    """Read, isolate, orient, sample and rig a head model into the dict
    HoloHead uses. Seconds for a big file the first time (call it off the GUI
    thread); a cache hit after that is ~20 ms."""
    path = Path(path)
    side = path.with_suffix(".json")
    cfg_text = side.read_text(encoding="utf-8") if side.exists() else ""
    cfg = json.loads(cfg_text) if cfg_text.strip() else {}
    fn = _cache_path(path, cfg_text)
    if fn.exists():
        try:
            mesh = _load(fn)
            mesh["cache_key"] = fn.stem
            return mesh
        except Exception:
            pass
    parts = _read_glb(path) if path.suffix.lower() == ".glb" else _read_obj(path)
    mesh = _process(parts, cfg)
    mesh["cache_key"] = fn.stem
    try:
        _save(fn, mesh)
    except OSError as e:
        print(f"[avatar] cache not written: {e}")
    return mesh


def discover() -> dict[str, Path]:
    """Model files in assets/avatars, by style key ("model:<name>")."""
    if not AVATAR_DIR.is_dir():
        return {}
    out = {}
    for p in sorted(AVATAR_DIR.iterdir()):
        if p.suffix.lower() in (".obj", ".glb") and not p.name.startswith("_"):
            out[f"model:{p.stem}"] = p
    return out
