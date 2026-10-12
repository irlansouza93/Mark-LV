"""
The holographic head, rendered as light.

`HoloHead` keeps every behaviour of `HoloAvatar` — lip-sync from the viseme
schedule, gaze, blinks, brows, state expressions, the sway — and replaces only
how it is drawn: instead of opaque flat-shaded triangles it is a dense cloud of
light sampled across the posed surface, lit by a Fresnel rim, cut by drifting
contour slices, and composed through `core.holo_engine` with bloom, scanlines
and optical fringing. See that module for why each ingredient is there.

The mesh comes from `core.avatar_mesh` (the measured MediaPipe face), or from
any OBJ handed to `HoloHead(mesh=...)` via `core.mesh_import`.
"""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtGui import QColor, QPainter

from core.avatar import HoloAvatar, _CAM_D
from core.holo_engine import budget_scale, draw_additive, surface_samples
from core.holo_gpu import make_buffer

# Surface samples. The GPU splats any number for free, so there the limit
# is the per-frame numpy that poses them (~4 ms at 60 k); the software
# backend pays per point and per pixel, so it gets fewer.
_N_GPU, _N_CPU = 44_000, 26_000
_PRI, _ACC, _VOL = 0, 1, 2   # buffer layers: crisp lines, accent, soft volume


class HoloHead(HoloAvatar):
    shaded = False
    HAS_BASE = True        # the HUD leaves room for the projector under the bust

    def __init__(self, mesh: dict | None = None) -> None:
        super().__init__()
        self.TOP = 1.0
        if mesh is not None:
            self._adopt(mesh)
        f = self._f
        v = self._v0
        # Denser where the eye goes: the face front gets three times the
        # points of the back of the skull and the neck.
        centre = v[f].mean(axis=1)
        front = np.clip(centre[:, 2] / 0.45, 0.0, 1.0) * self._fgroup
        self._buf = make_buffer(3, soft=(_VOL,))
        self._gpu = type(self._buf).__name__ == "GpuBuffer"
        self._n = _N_GPU if self._gpu else _N_CPU
        self._tri, self._bary = surface_samples(v, f, self._n,
                                                weight=1.0 + 2.0 * front)
        tv = f[self._tri]                                  # (n, 3) vertex ids
        self._tv = tv
        rest = np.einsum("nk,nkd->nd", self._bary, v[tv])
        self._y0 = rest[:, 1].astype(np.float32)           # model-space height
        self._fade_s = np.einsum("nk,nk->n", self._bary, self._fade[tv]).astype(np.float32)
        self._jit = np.random.default_rng(5).random(self._n).astype(np.float32)

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

    # ── drawing ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None) -> None:
        verts, norms = self._pose()
        t = self._t
        g = self._glow

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

        # ── the surface, as light ───────────────────────────────────────────
        tv, bary = self._tv, self._bary
        P = np.einsum("nk,nkd->nd", bary, verts[tv])
        N = np.einsum("nk,nkd->nd", bary, norms[tv])
        N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-6)
        xs, ys = proj(P)

        nz = N[:, 2]
        front = nz > 0.0
        # Contrast is the whole look: a dim, translucent body and a bright rim.
        # The body is only there to give the rim something to belong to.
        fres = np.clip(1.0 - np.abs(nz), 0.0, 1.0) ** 3.0
        key = np.clip(N[:, 0] * -0.45 + N[:, 1] * 0.55 + nz * 0.55, 0.0, 1.0)
        sweep = np.exp(-((self._y0 - self._scan) / 0.10) ** 2)
        flick = 0.92 + 0.08 * np.sin(t * 37.0 + self._jit * 40.0)
        body = (np.where(front, 0.05 + 0.20 * key ** 1.6, 0.025)
                + 0.85 * fres + 0.30 * sweep)
        body *= self._fade_s * flick * (0.85 + 0.35 * g)
        density = (r * r) / self._n * 2.2             # same brightness at any size
        # Sprites grow with the head so the cloud stays a surface, not dust.
        sprite = max(1.25, r / 120.0)
        buf.points(_VOL, xs, ys, body * density, radius=sprite)

        # Contour slices drifting up the head — the projector "building" it.
        # Drawn crisp, and only where a slice actually is.
        ph = (self._y0 * 14.0 - t * 0.35) % 1.0
        slices = np.exp(-((ph - 0.5) / 0.035) ** 2)
        on = slices > 0.08
        sv = slices[on] * np.where(front[on], 1.0, 0.25) * (0.35 + 0.5 * fres[on])
        buf.points(_PRI, xs[on], ys[on], sv * self._fade_s[on] * density * 2.4,
                   radius=max(1.0, sprite * 0.6))

        # ── sparse structure lines over the front ──────────────────────────
        ez = 0.5 * (norms[self._e0, 2] + norms[self._e1, 2])
        keep = np.flatnonzero(ez > 0.25)
        if keep.size:
            ex, ey = proj(verts)
            e0, e1 = self._e0[keep], self._e1[keep]
            buf.lines(_PRI, ex[e0], ey[e0], ex[e1], ey[e1],
                      0.10 * ez[keep] * self._fade[e0])

        # ── features ────────────────────────────────────────────────────────
        lm = self._lm
        face = max(0.0, math.cos(self._yaw) * math.cos(self._pitch)) ** 2
        if "eye_points" in lm and face > 0.02:
            # An imported model: eyes are points from its sidecar, the mouth a
            # glow at its mouth line — enough to read as alive and speaking.
            ep = np.asarray(lm["eye_points"], dtype=np.float32) @ self._rot.T
            exs, eys = proj(ep)
            open_ = 1.0 - self._blink
            for gx, gy in zip(exs, eys):
                buf.disc(_ACC, float(gx), float(gy), r * 0.07, (0.9 + 0.8 * g) * face * open_, 1.5)
                buf.disc(_ACC, float(gx), float(gy), r * 0.022, 2.2 * face * open_, 1.0)
            mc = (self._lip_c[None, :] @ self._rot.T)
            mx, my_ = proj(mc)
            if self._mouth > 0.03:
                buf.disc(_ACC, float(mx[0]), float(my_[0]), r * 0.13,
                         0.45 * self._mouth * face, 2.2, squash=0.5)
        if "eye_l" in lm and face > 0.02:
            vx, vy = proj(verts)
            open_ = 1.0 - self._blink
            for key_ in ("eye_l", "eye_r"):
                idx = lm[key_]
                ex, ey = vx[idx], vy[idx]
                my = float(ey.mean())
                ey = my + (ey - my) * max(0.05, open_)
                buf.polyline(_PRI, ex, ey, 0.9 * face, closed=True)
                if open_ > 0.3:
                    w_ = float(np.ptp(ex))
                    gx = float(ex.mean()) + self._gaze[0] * w_ * 0.16
                    gy = my + self._gaze[1] * float(np.ptp(ey)) * 0.2
                    rad = max(2.0, min(w_ * 0.19, float(np.ptp(ey)) * 0.6))
                    buf.disc(_ACC, gx, gy, rad * 1.9, (0.9 + 0.8 * g) * face * open_, 1.5)
                    buf.disc(_ACC, gx, gy, rad * 0.55, 2.4 * face * open_, 1.0)
            for key_ in ("brow_l", "brow_r"):
                idx = lm[key_]
                buf.polyline(_PRI, vx[idx], vy[idx], 0.55 * face)
            inner = lm["lips_in"]
            outer = lm["lips_out"]
            buf.polyline(_PRI, vx[outer], vy[outer], 0.45 * face, closed=True)
            buf.polyline(_PRI, vx[inner], vy[inner], (0.55 + 0.6 * self._mouth) * face,
                         closed=True)
            if self._mouth > 0.03:
                mx, my_ = float(vx[inner].mean()), float(vy[inner].mean())
                buf.disc(_ACC, mx, my_, float(np.ptp(vx[inner])) * 0.55,
                         0.45 * self._mouth * face, 2.2, squash=0.6)

        # ── projector base under the bust ──────────────────────────────────
        base_y = ocy + (self.SPAN - self.TOP + 0.10) * r
        bx = ocx
        a = np.linspace(0, 2 * np.pi, 220, endpoint=False)
        for rr, val in ((0.95, 0.50), (0.78, 0.30), (0.55, 0.65)):
            buf.polyline(_PRI, bx + np.cos(a) * rr * r, base_y + np.sin(a) * rr * 0.16 * r,
                         val * (0.8 + 0.5 * g), closed=True)
        spin = (t * 0.6) % (2 * np.pi)
        for k_ in range(3):
            aa = np.linspace(spin + k_ * 2.094, spin + k_ * 2.094 + 0.7, 40)
            buf.polyline(_ACC, bx + np.cos(aa) * 0.95 * r,
                         base_y + np.sin(aa) * 0.95 * 0.16 * r, 0.9)
        buf.disc(_PRI, bx, base_y, 0.55 * r, 0.30 + 0.25 * g, 2.0)

        img = buf.compose([primary, accent, primary], t, exposure=0.82, bloom=0.42,
                          scan=0.16, aberration=1,
                          glitch=0.6 if (t % 7.3) < 0.08 else 0.0)
        draw_additive(p, img, x0, y0, sc)
