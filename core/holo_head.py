"""
The holographic head, rendered as light.

`HoloHead` keeps every behaviour of `HoloAvatar` — lip-sync from the viseme
schedule, gaze, blinks, brows, state expressions, the sway — and replaces only
how it is drawn. Two renderers, mixed by the look (`core.holo_look`):

  * **Particles** — a dense cloud of light sampled across the posed surface,
    lit by a Fresnel rim, cut by drifting contour slices. Works everywhere,
    the numpy backend included.
  * **Surface** — the model's real triangles on the GPU with a game-hologram
    shader (`core.holo_surface`): depth-correct, textured, scan bands, noise.

Both are composed through the same buffer (`core.holo_engine` /
`core.holo_gpu`) with bloom, scanlines and optical fringing.

Everything a model needs to look and move like itself — colour from its own
texture, where its eyes and mouth are, how far its jaw drops, how its eyes
glow — is in `self.look`, which ⚙ SETUP → HOLOGRAMA edits live and saves per
style. The mesh comes from `core.avatar_mesh` (the measured MediaPipe face),
or from any model handed to `HoloHead(mesh=...)` via `core.mesh_import`.
"""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtGui import QColor, QPainter

from core import holo_look
from core.avatar import HoloAvatar, _CAM_D
from core.avatar_mesh import JAW_MAX
from core.holo_engine import (DepthGrid, budget_scale, draw_additive, draw_over,
                              surface_samples)
from core.holo_gpu import make_buffer

# Surface samples. The GPU splats any number for free, so there the limit
# is the per-frame numpy that poses them (~4 ms at 60 k); the software
# backend pays per point and per pixel, so it gets fewer.
_N_GPU, _N_CPU = 44_000, 26_000
_PRI, _ACC, _VOL = 0, 1, 2   # buffer layers: crisp lines, accent, soft volume
_LUM = np.array([0.2126, 0.7152, 0.0722], np.float32)


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def jaw_weights(q: np.ndarray, look: dict, zfront: float, pivot_z: float) -> np.ndarray:
    """How much of the jaw's rotation each point follows — the same formula
    the surface shader runs (core.holo_surface), so particles and surface open
    the same mouth. A mandible is rigid: everything a hair below the lips
    turns with it, nothing above the mouth line, nothing in the neck below the
    chin, nothing out at the ears. (A ramp from mouth to chin, the first try,
    left the lower lip still and the mouth looked shut at full volume.)"""
    my, chin, mw = float(look["mouth_y"]), float(look["chin_y"]), float(look["mouth_w"])
    sm = 0.03 + 0.32 * _smooth(0.55 * mw, 1.15 * mw, np.abs(q[:, 0]))   # as the shader's seam()
    j = np.clip((my - q[:, 1]) / sm, 0.0, 1.0)
    j = j * j * (3.0 - 2.0 * j)
    # Mouth corners stay tied (as the shader): see core.holo_surface.
    xn = np.clip(np.abs(q[:, 0]) / max(1e-3, mw), 0.0, 1.0) ** 2
    deep = 1.0 - _smooth(my - 0.16, my - 0.03, q[:, 1])
    j *= (1.0 - xn) + xn * deep
    j *= np.clip(0.3 + 0.85 * (q[:, 2] - pivot_z) / max(1e-3, zfront - pivot_z), 0.0, 1.0)
    j *= _smooth(chin - 0.30, chin - 0.05, q[:, 1])
    j *= 1.0 - _smooth(mw * 2.6, mw * 4.2, np.abs(q[:, 0]))
    return j.astype(np.float32)


class HoloHead(HoloAvatar):
    shaded = False
    HAS_BASE = True        # the HUD leaves room for the projector under the bust

    def __init__(self, mesh: dict | None = None, style: str = "face") -> None:
        super().__init__()
        self.TOP = 1.0
        self.style = style
        self.imported = mesh is not None
        self.anatomy = dict((mesh or {}).get("anatomy") or {})
        self.show_markers = False
        if mesh is not None:
            self._adopt(mesh)
        self._buf = make_buffer(3, soft=(_VOL,))
        self._gpu = type(self._buf).__name__ == "GpuBuffer"
        self._n = _N_GPU if self._gpu else _N_CPU
        S = (mesh or {}).get("samples")
        if S is None:
            # The built-in head: sample its surface here. Denser where the eye
            # goes — the face front gets three times the points of the back of
            # the skull and the neck.
            f, v = self._f, self._v0
            centre = v[f].mean(axis=1)
            front = np.clip(centre[:, 2] / 0.45, 0.0, 1.0) * self._fgroup
            tri, bary = surface_samples(v, f, self._n, weight=1.0 + 2.0 * front)
            tv = f[tri]

            def at(a):
                return np.einsum("nk,nk...->n...", bary, a[tv]).astype(np.float32)

            nrm = at(self._n0)
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-6)
            S = {"pos": at(v), "nrm": nrm, "jaw": at(self._jaw),
                 "brow": at(self._brow_w), "lips": at(self._lips_w),
                 "fade": at(self._fade), "alb": np.ones(len(tri), np.float32)}
        # Imported samples come shuffled, so the CPU backend's first 26 k are
        # an even subset of the GPU's 44 k.
        S = {k: np.ascontiguousarray(a[:self._n]) for k, a in S.items()}
        self._n = len(S["pos"])
        self._sp, self._sn = S["pos"], S["nrm"]
        self._sjaw0, self._sbrow, self._slips = S["jaw"], S["brow"], S["lips"]
        self._sjaw = self._sjaw0
        self._fade_s = S["fade"]
        self._alb = S["alb"]
        self._rgb = S.get("rgb", np.ones((self._n, 3), np.float32))
        fr = self._sp[:, 2] > 0.0
        lum = self._rgb @ _LUM
        self._lum_mid = float(np.median(lum[fr] if fr.any() else lum))
        self._zfront = float(np.percentile(self._sp[:, 2], 99))
        self._s_out = np.empty_like(self._sp)
        self._y0 = self._sp[:, 1].copy()                   # model-space height
        self._jit = np.random.default_rng(5).random(self._n).astype(np.float32)
        self._bottom = float(self._sp[:, 1].min())

        # The surface renderer's mesh: an import's full one, or the built-in
        # head's own. Uploaded the first time the look asks for a surface.
        g = (mesh or {}).get("gpu")
        if g is None and mesh is None:
            g = {"pos": self._v0, "nrm": self._n0, "idx": self._f.astype(np.uint32),
                 "uv": np.zeros((len(self._v0), 2), np.float32),
                 "ranges": np.array([[0, self._f.size, -1, 0, 1.0, 1.0, 1.0]]),
                 "textures": []}
        self._gmesh = g
        self._surf = None
        self.rig = None
        self._cache_key = (mesh or {}).get("cache_key")
        from core.blendshapes import from_gpu
        self.shapes = from_gpu(g) if (g is not None and self.imported) else None
        self.set_look(holo_look.resolve(style, self.anatomy, self._gpu))
        if self.imported and self.supports_surface:
            # The face's real landmarks (MediaPipe on a frontal render, once
            # per model, cached): an exact mouth line, lid rims and eyes.
            from core import face_rig
            pts = face_rig.load_or_detect(self, self._cache_key)
            if pts is not None:
                self.rig = face_rig.build(pts, {"pos": self._sp, "rgb": self._rgb},
                                          gpu_pos=self._gmesh["pos"])
                self.set_look(self.look)
            if self._surf is not None:          # drawn once for the detector
                self._surf.release()
                self._surf = None

    def _adopt(self, mesh: dict) -> None:
        """Swap in an imported mesh (see core.mesh_import)."""
        self._v0 = mesh["verts"]
        self._n0 = mesh["normals"]
        self._f = mesh["faces"]
        self._fa, self._fb, self._fc = (self._f[:, i] for i in range(3))
        self._jaw = mesh["jaw"]
        self._brow_w = mesh["brow"]
        self._lips_w = mesh["lips"]
        self._lip_c = mesh["lip_centre"]
        self._fade = mesh["fade"]
        self._fgroup = mesh["face_group"]
        self._e0, self._e1 = mesh["edges"][:, 0], mesh["edges"][:, 1]
        self._lm = mesh.get("landmarks") or {}
        self.TOP = mesh["span"][0]
        self.SPAN = mesh["span"][0] - mesh["span"][1]
        self._v = np.empty_like(self._v0)

    # ── the look ────────────────────────────────────────────────────────────
    @property
    def rig_kind(self) -> str:
        """What drives this face: the model's own blend shapes, landmarks
        found on it, or (no face found) a gentle jaw only."""
        if not self.imported:
            return "embutido"
        if self.shapes is not None and self.shapes.has_mouth:
            return "blendshapes"
        if self.rig is not None:
            return "landmarks"
        return "sem rosto"

    @property
    def supports_surface(self) -> bool:
        return bool(getattr(self._buf, "supports_mesh", False)) and self._gmesh is not None

    def set_look(self, look: dict) -> None:
        """Apply a look now (HOLOGRAMA calls this on every slider move)."""
        lk = dict(look)
        self.look = lk
        self.motion = float(lk.get("motion", 1.0))
        # Texture as a brightness gain averaging 1 over the face front: it
        # moves light from skin to eyes, brows and lips without dimming the
        # model (taken raw, Angelica's dark hair dimmed the whole hologram).
        a = np.clip(0.5 + (self._alb - 0.5) * float(lk["tex_contrast"]), 0.0, 1.0)
        k = 0.30 + 0.70 * a
        fr = self._sp[:, 2] > 0.0
        k = k / max(1e-3, float(k[fr].mean() if fr.any() else k.mean()))
        self._albk = (1.0 + float(lk["tex_detail"]) * (k - 1.0)).astype(np.float32)
        # Original colours for particles, scaled so the face's middle grey
        # carries about the light the theme colour would.
        self._rgbn = np.clip(self._rgb * (0.6 / max(0.05, self._lum_mid)), 0.0, 3.0)
        self._has_rgb = float(np.ptp(self._rgb)) > 1e-3 or float(self._rgb.mean()) < 0.99

        gain = float(lk["jaw"])
        if self.imported and getattr(self, "rig", None) is not None:
            from core import face_rig
            rg = self.rig
            # The rig's measurements win over any calibration: they are where
            # this face's features are, not a guess.
            e0, e1 = rg["eyes"]
            lk.update(mouth_y=rg["mouth_y"], mouth_w=rg["cw"], chin_y=rg["chin_y"],
                      eye_y=rg["eye_y"], eye_x=0.5 * (e1["x0"] + e1["x1"] - e0["x0"] - e0["x1"]) * 0.5,
                      eye_z=max(e0["z"], e1["z"]))
            self.look = lk
            pz = float(lk["pivot_z"])
            self._jaw_pivot = (0.0, rg["eye_y"] - 0.02, pz)
            self._sjaw = face_rig.jaw_weights(self._sp, rg, self._zfront, pz) * gain
            self._jaw = face_rig.jaw_weights(self._v0, rg, self._zfront, pz) * gain
            self._lip_c = np.array([rg["xc"], rg["mouth_y"], rg["mouth_z"]], np.float32)
            self._eyemask = None
        elif self.imported:
            # The built-in head also speaks with its lips and brows; a hologram
            # model only has its jaw, so it swings half as far again. A lit
            # character shows its open mouth plainly, so not there.
            gain *= 1.0 if holo_look.realistic(lk) else 1.5
            ey = float(lk["eye_y"])
            pz = float(lk["pivot_z"])
            # The hinge sits level with the eyes, a third of the way into the
            # head from the back — the built-in head's proportions.
            self._jaw_pivot = (0.0, ey - 0.02, pz)
            self._sjaw = jaw_weights(self._sp, lk, self._zfront, pz) * gain
            self._jaw = jaw_weights(self._v0, lk, self._zfront, pz) * gain
            self._lip_c = np.array([0.0, float(lk["mouth_y"]),
                                    float(self.anatomy.get("mouth_z", self._zfront - 0.2))],
                                   np.float32)
            # Particles inside each eye, for blinks.
            ex, ez = float(lk["eye_x"]), float(lk["eye_z"])
            rad = 0.075 * float(lk["eye_size"])
            m = np.zeros(self._n, np.float32)
            for s in (-1.0, 1.0):
                d = np.hypot((self._sp[:, 0] - s * ex) / (rad * 1.5), (self._sp[:, 1] - ey) / rad)
                m = np.maximum(m, 1.0 - _smooth(0.7, 1.1, d))
            self._eyemask = m * (self._sp[:, 2] > ez - 0.25)
        else:
            self._sjaw = self._sjaw0 * gain
            self._eyemask = None

    # ── drawing ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        lk = self.look
        t = self._t
        g = self._glow
        bright = float(lk["brightness"])

        # The buffer only covers the head and its base, not the whole HUD —
        # and on the software backend, at a capped resolution (see
        # budget_scale): everything below works in buffer pixels.
        x0 = cx - 1.25 * r
        y0 = cy - (self.TOP + 0.22) * r
        buf = self._buf
        sc = budget_scale(buf, r)
        r = r * sc
        bw = int(2.5 * r)
        bh = int((self.SPAN + 0.62) * r)
        buf.begin(bw, bh)
        ocx, ocy = 1.25 * r, (self.TOP + 0.22) * r       # head centre in the buffer

        def proj(q):
            w = np.maximum(_CAM_D - q[..., 2], 0.35)
            k = (_CAM_D / w) * r
            return ocx + q[..., 0] * k, ocy - q[..., 1] * k

        # Realistic: a lit, opaque character and nothing else — no particles,
        # bands, lines or glows, so none of the per-sample numpy runs either.
        real = holo_look.realistic(lk) and self.supports_surface
        verts, norms = self._pose()      # line vertices; sets the rotation
        if not real:
            P, N = self._pose_pts(self._sp, self._sn, self._s_out,
                                  self._sjaw, self._sbrow, self._slips)
            xs, ys = proj(P)
            nz = N[:, 2]
            front = nz > 0.0
            depth = DepthGrid(xs, ys, P[:, 2], bw, bh, int(front.sum()))
            occ = depth.light(xs, ys, P[:, 2], eps=0.07, behind=float(lk["inner"]))
        open_ = 1.0 - self._blink * float(lk["blink"])
        flick_g = (1.0 - float(lk["flicker"]) * 0.4 * (0.5 + 0.5 * math.sin(t * 23.0))) \
            * (0.85 + 0.35 * g)

        # ── surface: the real triangles, game-hologram shaded ───────────────
        surf_amt = float(lk["surface"])
        if (surf_amt > 0.005 or real) and self.supports_surface:
            if self._surf is None:
                from core.holo_surface import HoloSurface
                from core.blendshapes import from_gpu
                shapes = from_gpu(self._gmesh)
                if self.rig is not None and not (shapes is not None and shapes.has_mouth):
                    from core import face_rig
                    g2, jw = face_rig.augment(self._gmesh, self.rig, self._zfront,
                                              float(lk["pivot_z"]))
                    self._surf = HoloSurface(buf.ctx, g2, jaw=jw)
                    self._surf.set_face(self.rig)
                else:
                    self._surf = HoloSurface(buf.ctx, self._gmesh,
                                             jaw=None if self.imported else self._jaw_builtin())
                    if self.rig is not None:     # blend shapes for the mouth; lids from the rig
                        self._surf.set_face(self.rig)
            pv = self._jaw_pivot
            self._surf.draw(
                buf.direct_fbo, bw, bh, rot=self._rot, oc=(ocx, ocy), r=r, cam=_CAM_D,
                t=t, look=lk, primary=primary, accent=accent,
                jaw_ang=float(lk["jaw"]) * (1.5 if self.imported and not real else 1.0)
                * self._mouth * JAW_MAX,
                pivot=(pv[1], pv[2]),
                zfront=self._zfront, bottom=self._bottom, sweep_y=self._scan,
                flick=flick_g, lum_mid=self._lum_mid,
                blink=self._blink if self.imported else 0.0,
                mouth=self._mouth, wide=self._wide, mouth_z=float(self._lip_c[2]),
                gaze=(self._gaze[0], self._gaze[1]))

        # ── particles ───────────────────────────────────────────────────────
        pts_amt = 0.0 if real else float(lk["points"])
        density = (r * r) / self._n * 2.2             # same brightness at any size
        # Sprites grow with the head so the cloud stays a surface, not dust.
        sprite = max(1.0, r / 120.0 * float(lk["point_size"]))
        if pts_amt > 0.005:
            fres = np.clip(1.0 - np.abs(nz), 0.0, 1.0) ** float(lk["rim_power"])
            # Contrast is the whole look: a dim, translucent body and a bright
            # rim; "Preenchimento" sets how much body there is.
            key = np.clip(N[:, 0] * -0.45 + N[:, 1] * 0.55 + nz * 0.55, 0.0, 1.0)
            sweep = np.exp(-((self._y0 - self._scan) / 0.10) ** 2)
            flick = 1.0 - float(lk["flicker"]) * 0.53 * (0.5 + 0.5 * np.sin(t * 37.0 + self._jit * 40.0))
            fill = float(lk["fill"]) / 0.35
            body = (np.where(front, (0.09 + 0.30 * key ** 1.4) * fill, 0.025)
                    + 0.85 * float(lk["rim"]) * fres + float(lk["sweep"]) * sweep)
            body *= self._fade_s * flick * (0.85 + 0.35 * g) * occ * self._albk
            body *= pts_amt * bright * density
            if self._eyemask is not None and open_ < 0.999:
                body *= 1.0 - self._eyemask * (1.0 - open_) * 0.85
            mix = float(lk["tex_mix"]) if self._has_rgb else 0.0
            if mix < 0.999:
                buf.points(_VOL, xs, ys, body * (1.0 - mix), radius=sprite)
            if mix > 0.001:
                buf.points_rgb(xs, ys, self._rgbn, body * mix, radius=sprite, layer=_VOL)

        # Contour slices drifting up the head — the projector "building" it.
        sl_amt = 0.0 if real else float(lk["slices"]) * max(pts_amt, 0.35 if surf_amt > 0.005 else 0.0)
        if sl_amt > 0.005:
            fres = np.clip(1.0 - np.abs(nz), 0.0, 1.0) ** float(lk["rim_power"])
            ph = (self._y0 * float(lk["slice_density"]) - t * float(lk["slice_speed"])) % 1.0
            slices = np.exp(-((ph - 0.5) / 0.035) ** 2)
            on = slices > 0.08
            sv = slices[on] * occ[on] * (0.35 + 0.5 * fres[on])
            buf.points(_PRI, xs[on], ys[on],
                       sv * self._fade_s[on] * density * 1.7 * sl_amt * bright,
                       radius=max(1.0, sprite * 0.6))

        # ── sparse structure lines over the front ──────────────────────────
        ln_amt = 0.0 if real else float(lk["lines"])
        ez = 0.5 * (norms[self._e0, 2] + norms[self._e1, 2])
        keep = np.flatnonzero(ez > 0.25) if ln_amt > 0.005 else np.zeros(0, int)
        if keep.size:
            ex, ey = proj(verts)
            e0, e1 = self._e0[keep], self._e1[keep]
            mid = depth.light(0.5 * (ex[e0] + ex[e1]), 0.5 * (ey[e0] + ey[e1]),
                              0.5 * (verts[e0, 2] + verts[e1, 2]), eps=0.07, behind=0.0)
            buf.lines(_PRI, ex[e0], ey[e0], ex[e1], ey[e1],
                      0.10 * ez[keep] * self._fade[e0] * mid * ln_amt * bright)

        # ── features ────────────────────────────────────────────────────────
        face = max(0.0, math.cos(self._yaw) * math.cos(self._pitch)) ** 2
        if real:
            pass                         # the face itself carries eyes and mouth
        elif self.imported:
            self._paint_imported_features(buf, proj, r, g, face, open_)
        elif "eye_l" in self._lm and face > 0.02:
            self._paint_builtin_features(buf, proj, verts, g, face, open_)
        if self.show_markers:
            self._paint_markers(buf, proj, r)

        # ── projector base under the bust ──────────────────────────────────
        base = float(lk["base"])
        if base > 0.005:
            base_y = ocy + (self.SPAN - self.TOP + 0.10) * r
            bx = ocx
            a = np.linspace(0, 2 * np.pi, 220, endpoint=False)
            for rr, val in ((0.95, 0.50), (0.78, 0.30), (0.55, 0.65)):
                buf.polyline(_PRI, bx + np.cos(a) * rr * r, base_y + np.sin(a) * rr * 0.16 * r,
                             val * (0.8 + 0.5 * g) * base, closed=True)
            spin = (t * 0.6) % (2 * np.pi)
            for k_ in range(3):
                aa = np.linspace(spin + k_ * 2.094, spin + k_ * 2.094 + 0.7, 40)
                buf.polyline(_ACC, bx + np.cos(aa) * 0.95 * r,
                             base_y + np.sin(aa) * 0.95 * 0.16 * r, 0.9 * base)
            buf.disc(_PRI, bx, base_y, 0.55 * r, (0.30 + 0.25 * g) * base, 2.0)

        # A tear every few seconds; "Glitch" makes them stronger and closer.
        gl = float(lk["glitch"])
        period = max(1.5, 9.0 - 7.0 * gl)
        if real:
            img = buf.compose([primary, accent, primary], t, exposure=float(lk["exposure"]),
                              bloom=float(lk["bloom"]), scan=0.0, aberration=0,
                              glitch=0.0, tonemap=1)
            draw_over(p, img, x0, y0, sc)
            return
        img = buf.compose([primary, accent, primary], t, exposure=float(lk["exposure"]),
                          bloom=float(lk["bloom"]), scan=float(lk["scan"]),
                          aberration=int(round(float(lk["aberration"]))),
                          glitch=gl if (gl > 0.01 and (t % period) < 0.08) else 0.0)
        draw_additive(p, img, x0, y0, sc)

    def _jaw_builtin(self) -> np.ndarray:
        return np.asarray(self._jaw, np.float32)

    def _paint_imported_features(self, buf, proj, r, g, face, open_) -> None:
        lk = self.look
        style = lk["eye_style"]
        if face > 0.02 and style in ("glow", "iris"):
            ex, ey, ez = float(lk["eye_x"]), float(lk["eye_y"]), float(lk["eye_z"])
            ep = np.array([[-ex, ey, ez], [ex, ey, ez]], np.float32) @ self._rot.T
            exs, eys = proj(ep)
            size, glow = float(lk["eye_size"]), float(lk["eye_glow"])
            gz = float(lk["gaze"])
            for gx, gy in zip(exs, eys):
                gx = float(gx) + self._gaze[0] * r * 0.018 * gz * size
                gy = float(gy) + self._gaze[1] * r * 0.012 * gz * size
                if style == "glow":
                    buf.disc(_ACC, gx, gy, r * 0.06 * size,
                             (0.5 + 0.6 * g) * face * open_ * glow, 1.5)
                    buf.disc(_ACC, gx, gy, r * 0.018 * size, 1.3 * face * open_ * glow, 1.0)
                else:
                    # An iris: a thin ring and a pupil, squashed shut by blinks.
                    a = np.linspace(0, 2 * np.pi, 48, endpoint=False)
                    rad = r * 0.032 * size
                    buf.polyline(_ACC, gx + np.cos(a) * rad,
                                 gy + np.sin(a) * rad * max(0.08, open_),
                                 0.7 * face * glow, closed=True)
                    buf.disc(_ACC, gx, gy, rad * 0.45, 1.4 * face * open_ * glow, 1.0)
                    buf.disc(_ACC, gx, gy, rad * 1.8, 0.25 * face * open_ * glow, 2.0)
        mg = float(lk["mouth_glow"])
        if mg > 0.005 and self._mouth > 0.03:
            mc = self._lip_c[None, :].copy()
            mc[0, 1] -= 0.05 * self._mouth          # the gap opens downward
            mx, my_ = proj(mc @ self._rot.T)
            buf.disc(_ACC, float(mx[0]), float(my_[0]), r * float(lk["mouth_w"]) * 0.9,
                     mg * self._mouth * face, 2.2, squash=0.45)

    def _paint_builtin_features(self, buf, proj, verts, g, face, open_) -> None:
        lk = self.look
        lm = self._lm
        vx, vy = proj(verts)
        glow = float(lk["eye_glow"]) if lk["eye_style"] in ("glow", "iris") else 0.0
        gz = float(lk["gaze"])
        for key_ in ("eye_l", "eye_r"):
            idx = lm[key_]
            ex, ey = vx[idx], vy[idx]
            my = float(ey.mean())
            ey = my + (ey - my) * max(0.05, open_)
            buf.polyline(_PRI, ex, ey, 0.9 * face, closed=True)
            if open_ > 0.3 and glow > 0.0:
                w_ = float(np.ptp(ex))
                gx = float(ex.mean()) + self._gaze[0] * w_ * 0.16 * gz
                gy = my + self._gaze[1] * float(np.ptp(ey)) * 0.2 * gz
                rad = max(2.0, min(w_ * 0.19, float(np.ptp(ey)) * 0.6)) * float(lk["eye_size"])
                buf.disc(_ACC, gx, gy, rad * 1.9, (0.9 + 0.8 * g) * face * open_ * glow, 1.5)
                buf.disc(_ACC, gx, gy, rad * 0.55, 2.4 * face * open_ * glow, 1.0)
        for key_ in ("brow_l", "brow_r"):
            idx = lm[key_]
            buf.polyline(_PRI, vx[idx], vy[idx], 0.55 * face)
        inner = lm["lips_in"]
        outer = lm["lips_out"]
        buf.polyline(_PRI, vx[outer], vy[outer], 0.45 * face, closed=True)
        buf.polyline(_PRI, vx[inner], vy[inner], (0.55 + 0.6 * self._mouth) * face,
                     closed=True)
        mg = float(lk["mouth_glow"]) / 0.45
        if self._mouth > 0.03 and mg > 0.0:
            mx, my_ = float(vx[inner].mean()), float(vy[inner].mean())
            buf.disc(_ACC, mx, my_, float(np.ptp(vx[inner])) * 0.55,
                     0.45 * self._mouth * face * mg, 2.2, squash=0.6)

    def _paint_markers(self, buf, proj, r) -> None:
        """Calibration guides: eyes, mouth line, chin line and the jaw hinge,
        where the look currently puts them."""
        lk = self.look
        if not self.imported:
            return
        if self.rig is not None:
            # The rig's own lines: the lips' meeting line and each lid's rim.
            rg = self.rig
            sm = rg["seam"]
            q = np.c_[sm[:, :2], np.full(len(sm), rg["mouth_z"])].astype(np.float32) @ self._rot.T
            x, y = proj(q)
            buf.polyline(_ACC, x, y, 1.6)
            for e in rg["eyes"]:
                xs = np.linspace(e["x0"], e["x1"], len(e["up"]))
                for ys in (e["up"], e["lo"]):
                    q = np.c_[xs, ys, np.full(len(xs), e["z"])].astype(np.float32) @ self._rot.T
                    x, y = proj(q)
                    buf.polyline(_ACC, x, y, 1.4)
            return
        ex, ey, ez = float(lk["eye_x"]), float(lk["eye_y"]), float(lk["eye_z"])
        my, mw, ch = float(lk["mouth_y"]), float(lk["mouth_w"]), float(lk["chin_y"])
        mz = float(self._lip_c[2])

        def seg(a, b, val=1.6):
            q = np.array([a, b], np.float32) @ self._rot.T
            x, y = proj(q)
            buf.lines(_ACC, x[:1], y[:1], x[1:], y[1:], val)

        c = 0.05
        ew = float(lk.get("eye_w", 0.085)) * float(lk["eye_size"])
        eh = float(lk.get("eye_h", 0.05)) * float(lk["eye_size"])
        a_ = np.linspace(0, 2 * np.pi, 40)
        for s in (-1, 1):
            seg((s * ex - c * 0.5, ey, ez), (s * ex + c * 0.5, ey, ez))
            seg((s * ex, ey - c * 0.5, ez), (s * ex, ey + c * 0.5, ez))
            # The opening a blink covers: fit it to the eye.
            ring = np.c_[s * ex + np.cos(a_) * ew, ey + np.sin(a_) * eh,
                         np.full(len(a_), ez)].astype(np.float32) @ self._rot.T
            rx, ry = proj(ring)
            buf.polyline(_ACC, rx, ry, 1.2)
        seg((-mw, my, mz), (mw, my, mz))
        seg((-mw * 0.6, ch, mz - 0.1), (mw * 0.6, ch, mz - 0.1), 1.0)
        pv = self._jaw_pivot
        seg((0.0, pv[1] - c, pv[2]), (0.0, pv[1] + c, pv[2]), 1.0)
        seg((-c, pv[1], pv[2]), (c, pv[1], pv[2]), 1.0)
