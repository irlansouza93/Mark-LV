"""
Holographic rendering engine — light, not paint.

Why this exists
---------------
The first HUD styles drew opaque, flat-shaded triangles: the look of a 1998
game engine, which is exactly what a hologram is not. Every convincing film and
game hologram (Cantina Creative's Iron Man work, the usual three.js / Godot
hologram shaders) is built from the same few ingredients, and this module
implements them in numpy so the HUD still needs no GPU:

  * **Additive light.** Nothing occludes anything. Every element *adds* light
    to an accumulation buffer, so the inside of the head shows faintly through
    its skin and overlaps glow brighter — the hologram is a volume, not a shell.
  * **Fresnel rim.** Surfaces brighten as they turn away from the viewer
    (pow(1 - |n·v|, k)). This is what draws the silhouette and the anatomy.
  * **Smooth normals.** Points are sampled across each triangle with
    barycentric weights and their normals interpolated, so a 1.5 k-triangle
    mesh lights like a smooth sculpt — no facets.
  * **Bloom.** The buffer is blurred at three scales and added back, so bright
    areas bleed light the way an emissive display does.
  * **Scanlines, slices and flicker** in screen and model space, plus a
    whisper of chromatic separation — the "projected" quality.

The buffer holds scalar light per *layer* (primary and accent), coloured only at
the end, so the live theme still recolours everything for free.
"""

from __future__ import annotations

import math

import numpy as np
from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QColor, QImage, QPainter


def _rgb(col: QColor) -> np.ndarray:
    return np.array([col.redF(), col.greenF(), col.blueF()], dtype=np.float32)


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Separable box blur over the last two axes, via cumulative sums — O(n)
    whatever the radius, and vectorised across layers."""
    if r < 1:
        return a
    k = 2 * r + 1
    pad = [(0, 0)] * (a.ndim - 2)
    p = np.pad(a, pad + [(r + 1, r), (0, 0)], mode="edge")
    c = np.cumsum(p, axis=-2, dtype=np.float32)
    a = (c[..., k:, :] - c[..., :-k, :]) / k
    p = np.pad(a, pad + [(0, 0), (r + 1, r)], mode="edge")
    c = np.cumsum(p, axis=-1, dtype=np.float32)
    return (c[..., k:] - c[..., :-k]) / k


def _down2(a: np.ndarray) -> np.ndarray:
    h, w = (a.shape[-2] // 2) * 2, (a.shape[-1] // 2) * 2
    a = a[..., :h, :w]
    return 0.25 * (a[..., 0::2, 0::2] + a[..., 1::2, 0::2]
                   + a[..., 0::2, 1::2] + a[..., 1::2, 1::2])


def _up_add(dst: np.ndarray, a: np.ndarray, f: int, weight: float) -> None:
    u = np.repeat(np.repeat(a, f, axis=-2), f, axis=-1)
    h, w = min(dst.shape[-2], u.shape[-2]), min(dst.shape[-1], u.shape[-1])
    dst[..., :h, :w] += u[..., :h, :w] * weight


class HoloBuffer:
    """An additive light buffer with N scalar layers, composed to a QImage.

    Splats are queued and resolved with ONE bincount per layer at compose time:
    resolving each call separately allocated a full-frame array four times per
    call, and that alone was half of a 57 ms frame.

    `soft` layers get a one-pixel blur before composing. The surface cloud goes
    there: a few points per pixel read as static when left raw, and as a
    smooth translucent volume once softened — while lines and slices, in the
    crisp layers, keep their edge.
    """

    def __init__(self, layers: int = 2, soft: tuple[int, ...] = ()) -> None:
        self.n = layers
        self.soft = soft
        self.w = self.h = 0
        self.buf = np.zeros((layers, 1, 1), dtype=np.float32)
        self._idx: list[list[np.ndarray]] = [[] for _ in range(layers)]
        self._val: list[list[np.ndarray]] = [[] for _ in range(layers)]

    def begin(self, w: int, h: int) -> None:
        w, h = max(8, int(w)), max(8, int(h))
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self.buf = np.zeros((self.n, h, w), dtype=np.float32)
        else:
            self.buf.fill(0.0)
        for i in range(self.n):
            self._idx[i].clear()
            self._val[i].clear()

    # ── primitives ──────────────────────────────────────────────────────────
    def points(self, layer: int, xs, ys, val, radius: float = 1.0) -> None:
        """Queue points with bilinear weights (sub-pixel smooth, no aliasing).
        `radius` is the GPU backend's sprite size; splats here are always one
        pixel, which is what keeps this backend affordable."""
        xs = np.asarray(xs, dtype=np.float32).ravel()
        ys = np.asarray(ys, dtype=np.float32).ravel()
        val = np.broadcast_to(np.asarray(val, dtype=np.float32), xs.shape).ravel()
        ok = (xs >= 0) & (ys >= 0) & (xs < self.w - 1) & (ys < self.h - 1) & (val > 1e-4)
        if not ok.any():
            return
        xs, ys, val = xs[ok], ys[ok], val[ok]
        x0 = xs.astype(np.int32)
        y0 = ys.astype(np.int32)
        fx, fy = xs - x0, ys - y0
        i0 = y0 * self.w + x0
        gx, gy = 1.0 - fx, 1.0 - fy
        self._idx[layer].append(np.concatenate([i0, i0 + 1, i0 + self.w, i0 + self.w + 1]))
        self._val[layer].append(np.concatenate([val * gx * gy, val * fx * gy,
                                                val * gx * fy, val * fx * fy]))

    def lines(self, layer: int, x0, y0, x1, y1, val, spacing: float = 0.8,
              width: float = 1.0) -> None:
        """Additive anti-aliased segments, rasterised as dense point runs.
        (`width` is honoured by the GPU backend only.)"""
        x0, y0, x1, y1 = (np.asarray(a, dtype=np.float32).ravel() for a in (x0, y0, x1, y1))
        if x0.size == 0:
            return
        val = np.broadcast_to(np.asarray(val, dtype=np.float32).ravel(), x0.shape)
        L = np.hypot(x1 - x0, y1 - y0)
        n = np.clip((L / spacing).astype(np.int32) + 1, 1, 400)
        seg = np.repeat(np.arange(x0.size), n)
        start = np.cumsum(n) - n
        t = (np.arange(seg.size) - start[seg]) / np.maximum(n[seg], 1)
        xs = x0[seg] + (x1 - x0)[seg] * t
        ys = y0[seg] + (y1 - y0)[seg] * t
        self.points(layer, xs, ys, val[seg] * spacing)

    def polyline(self, layer: int, xs, ys, val, closed: bool = False) -> None:
        xs, ys = np.asarray(xs, np.float32), np.asarray(ys, np.float32)
        if closed:
            xs, ys = np.append(xs, xs[0]), np.append(ys, ys[0])
        self.lines(layer, xs[:-1], ys[:-1], xs[1:], ys[1:], val)

    def disc(self, layer: int, cx: float, cy: float, r: float, val: float,
             falloff: float = 2.0, squash: float = 1.0) -> None:
        """A soft radial glow, e.g. a core, an iris or an emitter."""
        ry = r * squash
        x0, x1 = int(max(0, cx - r)), int(min(self.w, cx + r + 1))
        y0, y1 = int(max(0, cy - ry)), int(min(self.h, cy + ry + 1))
        if x1 <= x0 or y1 <= y0:
            return
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d = np.hypot((xx - cx) / max(1.0, r), (yy - cy) / max(1.0, ry))
        self.buf[layer, y0:y1, x0:x1] += val * np.clip(1.0 - d, 0.0, 1.0) ** falloff

    def _resolve(self) -> None:
        size = self.w * self.h
        for i in range(self.n):
            if self._idx[i]:
                idx = np.concatenate(self._idx[i])
                val = np.concatenate(self._val[i])
                self.buf[i].reshape(-1)[:] += np.bincount(idx, weights=val,
                                                          minlength=size)[:size]

    # ── finishing ───────────────────────────────────────────────────────────
    def compose(self, colors: list[QColor], t: float, exposure: float = 1.0,
                bloom: float = 0.9, scan: float = 0.18, aberration: int = 1,
                glitch: float = 0.0) -> QImage:
        """Bloom, colour, scanlines and tone-map the layers into an RGB image.
        `colors` has one entry per layer."""
        self._resolve()
        L = self.buf
        for i in self.soft:
            L[i] = _box_blur(L[i], 1)
        h, w = self.h, self.w

        # Layers that share a colour are summed first: bloom and colouring then
        # run on as few planes as there are distinct colours.
        keys, planes, cols = [], [], []
        for i, c in enumerate(colors[:self.n]):
            k = c.rgb()
            if k in keys:
                planes[keys.index(k)] = planes[keys.index(k)] + L[i]
            else:
                keys.append(k)
                planes.append(L[i])
                cols.append(_rgb(c))
        L = np.stack(planes)

        if bloom > 0:
            # A blur pyramid: three octaves, each at a quarter of the pixels of
            # the one before, folded back up level by level so only ONE
            # upsample ever happens at full resolution.
            lv1 = _down2(L)
            lv2 = _down2(lv1)
            lv3 = _down2(lv2)
            b3 = _box_blur(_box_blur(lv3, 3), 3) * 0.40
            b2 = _box_blur(_box_blur(lv2, 2), 2) * 0.45
            _up_add(b2, b3, 2, 1.0)
            b1 = _box_blur(lv1, 2) * 0.55
            _up_add(b1, b2, 2, 1.0)
            _up_add(L, b1, 2, bloom)

        rgb = np.einsum("lhw,lc->hwc", L, np.stack(cols), optimize=True)

        if scan > 0:
            # Fine raster lines fixed to the screen, plus a slow bright band
            # rolling down it — the projector's refresh, not the model's.
            y = np.arange(h, dtype=np.float32)
            rows = 1.0 - scan * (0.5 + 0.5 * np.cos(y * (2 * math.pi / 3.0)))
            band = 1.0 + 0.30 * np.exp(-(((y - (t * 90.0) % (h + 200) + 100)) / 28.0) ** 2)
            rgb *= (rows * band)[:, None, None]

        if glitch > 0.01:
            # A rare horizontal tear: a band of rows slides sideways briefly.
            band_y = int((math.sin(t * 13.7) * 0.5 + 0.5) * (h - 20))
            bh = 4 + int(10 * glitch)
            shift = int(6 * glitch * (1 if math.sin(t * 91.0) > 0 else -1))
            rgb[band_y:band_y + bh] = np.roll(rgb[band_y:band_y + bh], shift, axis=1)

        if aberration:
            # Red and blue a pixel apart: the faint fringing of light through
            # optics. Subtle, but it is what sells "projected".
            a = int(aberration)
            rgb[:, a:, 0] = rgb[:, :-a, 0]
            rgb[:, :-a, 2] = rgb[:, a:, 2]

        img = 1.0 - np.exp(-rgb * exposure)          # filmic shoulder: never clips hard
        out = np.ascontiguousarray((img * 255.0).astype(np.uint8))
        qi = QImage(out.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        qi._keep = out                                 # the QImage borrows this memory
        return qi


def draw_additive(p: QPainter, img: QImage, x: float, y: float,
                  scale: float = 1.0) -> None:
    """Blend a composed buffer onto the HUD with additive ('Plus') light.
    `scale` < 1 means the buffer was rendered at reduced resolution and is
    stretched back to size (the software backend's budget at large sizes)."""
    mode = p.compositionMode()
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale < 1.0)
    p.drawImage(QRectF(x, y, img.width() / scale, img.height() / scale), img)
    p.setCompositionMode(mode)


def budget_scale(buffer, r: float, cap: float = 170.0) -> float:
    """Resolution scale for a renderer: 1 on the GPU, capped in software so a
    full-screen hologram costs what a 170 px one does."""
    return 1.0 if type(buffer).__name__ == "GpuBuffer" else min(1.0, cap / max(1.0, r))


def surface_samples(verts: np.ndarray, faces: np.ndarray, n: int,
                    weight: np.ndarray | None = None, seed: int = 3):
    """Area-weighted random points on a mesh, as (triangle index, barycentric).

    Sampling once and re-using the same barycentric coordinates every frame
    means the points ride the posed mesh — jaw, brows and all — and never
    shimmer between frames.
    """
    rng = np.random.default_rng(seed)
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    if weight is not None:
        area = area * weight
    prob = area / area.sum()
    tri = rng.choice(len(faces), size=n, p=prob)
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1.0
    u[flip], v[flip] = 1.0 - u[flip], 1.0 - v[flip]
    bary = np.stack([1.0 - u - v, u, v], axis=1).astype(np.float32)
    return tri.astype(np.int32), bary
