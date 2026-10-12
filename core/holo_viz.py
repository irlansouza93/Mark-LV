"""
Holographic voice visualisers for the HUD centre — the two non-face styles.

Both follow the rules the avatar and the reactor core set:

* **Software rendered.** QPainter and numpy only — no OpenGL context, no GPU,
  no new dependency. The point clouds are projected with the same perspective
  camera the head uses.
* **Everything moves for a reason.** Rotation speed is the assistant's state
  (idle drifts, thinking hurries, speaking runs); size and brightness are the
  real audio level the HUD already smooths for the waveform.
* **Theme-agnostic.** `paint()` takes its colours as arguments, so the hue wheel
  retints them like everything else.

Shared lifecycle with `HoloAvatar`:
    viz.step(dt, amp, speaking=..., muted=..., state=...)   # every tick
    viz.paint(painter, cx, cy, r, primary, accent, bg)      # every frame

Styles
------
`HoloOrb`        — a sphere of light points that ripples outward with the voice,
                   wired to its neighbours, inside three tilted orbit rings.
`HoloProjector`  — a projector base casting a beam, and above it a 3-D sculpture
                   of the voice's recent history: one ring per moment, newest at
                   the emitter, rising and fading as it ages.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                         QPolygonF, QRadialGradient)

_CAM_D = 4.2       # perspective camera distance, in units of the shape radius
_BUCKETS = 5       # opacity buckets: lines and points draw in a few batched calls


def _c(col: QColor, a: float) -> QColor:
    q = QColor(col)
    q.setAlpha(int(max(0.0, min(255.0, a))))
    return q


def _blend(bg: QColor, col: QColor, a: float) -> QColor:
    """`col` at alpha `a` (0..1) pre-mixed onto `bg`, returned opaque — Qt's
    opaque line path is several times faster than its blended one."""
    f = max(0.0, min(1.0, a))
    return QColor(int(bg.red() + (col.red() - bg.red()) * f),
                  int(bg.green() + (col.green() - bg.green()) * f),
                  int(bg.blue() + (col.blue() - bg.blue()) * f))


def _rot(yaw: float, pitch: float) -> np.ndarray:
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    return np.array([[cy, 0.0, sy],
                     [sp * sy, cp, -sp * cy],
                     [-cp * sy, sp, cp * cy]], dtype=np.float32)


def _project(q: np.ndarray, cx: float, cy: float, r: float):
    w = np.maximum(_CAM_D - q[:, 2], 0.4)
    k = (_CAM_D / w) * r
    return cx + q[:, 0] * k, cy - q[:, 1] * k


class _Base:
    """State shared by both visualisers: time, energy and spin."""

    TOP = 1.0
    SPAN = 2.0

    def __init__(self) -> None:
        self._t = 0.0
        self._spin = 0.0
        self._glow = 0.0
        self._speaking = False
        self._muted = False
        self._state = ""

    def glance(self, *_a, **_k) -> None:      # the HUD may call this on any style
        pass

    def step(self, dt: float, amp: float, speaking: bool = False,
             muted: bool = False, state: str = "", **_ignored) -> None:
        dt = max(0.001, min(0.10, float(dt)))
        self._t += dt
        amp = 0.0 if muted else max(0.0, min(1.0, float(amp)))
        self._glow += (amp - self._glow) * (0.35 if amp > self._glow else 0.10)
        self._speaking, self._muted = bool(speaking), bool(muted)
        self._state = (state or "").upper()
        thinking = self._state in ("THINKING", "PROCESSING")
        asleep = self._state in ("SLEEPING", "STANDBY", "OFFLINE")
        # Integrated, never wall-clock × rate: a rate change must not jump the
        # phase (the same lesson the head's sway and the core's rings taught).
        rate = 0.18 if asleep else (0.32 + (0.9 if thinking else 0.0)
                                    + (0.45 if speaking else 0.0))
        self._spin += dt * rate
        self._advance(dt, amp)

    def _advance(self, dt: float, amp: float) -> None:
        pass


class HoloOrb(_Base):
    """A sphere of light that breathes with the voice."""

    def __init__(self, n: int = 460) -> None:
        super().__init__()
        # Fibonacci sphere: evenly spread points with no seam or pole cluster.
        i = np.arange(n, dtype=np.float32) + 0.5
        phi = np.arccos(1.0 - 2.0 * i / n)
        theta = np.pi * (1.0 + 5.0 ** 0.5) * i
        self._p0 = np.stack([np.cos(theta) * np.sin(phi), np.cos(phi),
                             np.sin(theta) * np.sin(phi)], axis=1).astype(np.float32)
        self._lat = phi.astype(np.float32)
        self._lon = (theta % (2 * np.pi)).astype(np.float32)

        # Neighbour wiring, computed once: each point to its 3 nearest. Kept
        # sparse — a dense plexus at this size reads as grey fog.
        d = np.linalg.norm(self._p0[:, None, :] - self._p0[None, :, :], axis=2)
        np.fill_diagonal(d, 9.0)
        nn = np.argsort(d, axis=1)[:, :3]
        pairs = {(min(a, b), max(a, b)) for a in range(n) for b in nn[a]}
        e = np.array(sorted(pairs), dtype=np.int32)
        self._e0, self._e1 = e[:, 0], e[:, 1]

        # Three orbit rings, each a tilted circle with a travelling marker.
        a = np.linspace(0.0, 2 * np.pi, 96, endpoint=False, dtype=np.float32)
        self._ring = np.stack([np.cos(a), np.zeros_like(a), np.sin(a)], axis=1)
        self._rings = ((1.42, 0.42, 0.0, 1.0), (1.62, -0.28, 1.2, -0.7),
                       (1.86, 1.15, 2.1, 0.45))     # radius, tilt, yaw, speed

    def _shape(self) -> np.ndarray:
        """Sphere displaced outward by a travelling interference pattern whose
        depth is the voice — still and round when silent, rippling when not."""
        t = self._t
        g = self._glow
        wave = (np.sin(self._lat * 5.0 + t * 2.4) * np.cos(self._lon * 3.0 - t * 1.7)
                + 0.6 * np.sin(self._lat * 9.0 - t * 3.1 + self._lon * 2.0))
        r = 1.0 + 0.012 * math.sin(t * 1.3) + (0.05 + 0.24 * g) * g * wave
        return self._p0 * r[:, None].astype(np.float32)

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None,
              W: float = 0.0, H: float = 0.0) -> None:
        bg = bg or QColor(0, 0, 0)
        g = self._glow
        r *= 0.50                                     # rings reach 1.86 r
        R = _rot(self._spin, 0.38 + 0.08 * math.sin(self._t * 0.21))

        # Core light — the orb's body before any point is read.
        for gr, a in ((r * 1.9, 0.20), (r * 0.9, 0.30 + 0.35 * g)):
            grad = QRadialGradient(cx, cy, gr)
            grad.setColorAt(0.0, _c(primary, 255 * a))
            grad.setColorAt(0.55, _c(primary, 255 * a * 0.35))
            grad.setColorAt(1.0, _c(primary, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Back halves of the rings first, so the sphere passes in front of them.
        rings = []
        for rad, tilt, yaw0, spd in self._rings:
            M = R @ _rot(yaw0 + self._spin * spd, tilt)
            q = (self._ring * rad) @ M.T
            xs, ys = _project(q, cx, cy, r)
            rings.append((q, xs, ys, spd))
        self._paint_rings(p, rings, primary, accent, bg, back=True)

        q = self._shape() @ R.T
        xs, ys = _project(q, cx, cy, r)
        depth = (q[:, 2] + 1.3) / 2.6                 # 0 back .. 1 front

        # Wiring.
        ea = 0.5 * (depth[self._e0] + depth[self._e1])
        ea = (0.10 + 0.55 * ea ** 1.6) * (0.75 + 0.6 * g)
        b = np.clip((ea * _BUCKETS).astype(np.int32), 0, _BUCKETS - 1)
        quad = np.stack([xs[self._e0], ys[self._e0], xs[self._e1], ys[self._e1]], 1)
        for k in range(_BUCKETS):
            sel = quad[b == k]
            if len(sel):
                p.setPen(QPen(_blend(bg, primary, (k + 0.5) / _BUCKETS * 0.75), 1.0))
                p.drawLines([QLineF(*row) for row in sel.tolist()])

        # Points: brighter and larger toward the viewer; the accent lights the
        # front cap when there is a voice.
        pb = np.clip((depth * _BUCKETS).astype(np.int32), 0, _BUCKETS - 1)
        for k in range(_BUCKETS):
            sel = np.flatnonzero(pb == k)
            if not len(sel):
                continue
            f = (k + 0.5) / _BUCKETS
            col = accent if (k == _BUCKETS - 1 and g > 0.08) else primary
            p.setPen(QPen(_blend(bg, col, 0.30 + 0.70 * f), 1.2 + 2.2 * f * (0.8 + g),
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawPoints(QPolygonF([QPointF(float(xs[i]), float(ys[i])) for i in sel]))

        self._paint_rings(p, rings, primary, accent, bg, back=False)

    def _paint_rings(self, p, rings, primary, accent, bg, back: bool) -> None:
        for q, xs, ys, spd in rings:
            z = q[:, 2]
            n = len(z)
            lines = []
            for i in range(n):
                j = (i + 1) % n
                if (z[i] + z[j] < 0) == back:
                    lines.append(QLineF(float(xs[i]), float(ys[i]),
                                        float(xs[j]), float(ys[j])))
            p.setPen(QPen(_blend(bg, primary, 0.22 if back else 0.55), 1.1))
            if lines:
                p.drawLines(lines)
            # A marker running along the ring, at the ring's own speed.
            m = int((self._t * 18.0 * abs(spd) * (1 if spd > 0 else -1)) % n)
            if (z[m] < 0) == back:
                p.setPen(QPen(_blend(bg, accent, 0.45 if back else 1.0), 5.0,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawPoint(QPointF(float(xs[m]), float(ys[m])))


class HoloProjector(_Base):
    """A holographic projector rendering the voice as a rising sculpture."""

    _RINGS = 34          # history depth: one ring per sample
    _SEGS = 56           # points around each ring
    _MERIDIANS = 14      # vertical wires tying the rings into a surface
    _HOP = 0.055         # seconds between history samples

    def __init__(self) -> None:
        super().__init__()
        self._hist = deque([0.0] * self._RINGS, maxlen=self._RINGS)
        self._acc = 0.0
        self._peak = 0.0
        a = np.linspace(0.0, 2 * np.pi, self._SEGS, endpoint=False, dtype=np.float32)
        self._ca, self._sa = np.cos(a), np.sin(a)
        self._a = a
        rng = np.random.default_rng(11)
        self._motes = rng.random((48, 3)).astype(np.float32)   # x, phase, speed

    def _advance(self, dt: float, amp: float) -> None:
        # Sample the level at a fixed rate, so the sculpture's height is a
        # fixed span of time regardless of the paint rate. The peak within each
        # hop is kept, so a short syllable is never sampled away.
        self._peak = max(self._peak, amp)
        self._acc += dt
        while self._acc >= self._HOP:
            self._acc -= self._HOP
            self._hist.appendleft(self._peak)
            self._peak = 0.0

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None,
              W: float = 0.0, H: float = 0.0) -> None:
        bg = bg or QColor(0, 0, 0)
        g = self._glow
        t = self._t
        pitch = 0.42
        R = _rot(self._spin * 0.9, pitch)

        base_y = cy + r * 0.86                 # the emitter's centre on screen
        col_h = r * 1.55                       # how tall the sculpture stands
        rad0 = r * 0.62

        # ── beam ───────────────────────────────────────────────────────────
        top_y = base_y - col_h * 1.05
        beam = QPolygonF([QPointF(cx - rad0 * 0.55, base_y),
                          QPointF(cx + rad0 * 0.55, base_y),
                          QPointF(cx + rad0 * 1.25, top_y),
                          QPointF(cx - rad0 * 1.25, top_y)])
        lg = QLinearGradient(cx, base_y, cx, top_y)
        lg.setColorAt(0.0, _c(primary, 70 + 70 * g))
        lg.setColorAt(0.6, _c(primary, 22 + 25 * g))
        lg.setColorAt(1.0, _c(primary, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(lg))
        p.drawPolygon(beam)
        p.setBrush(Qt.BrushStyle.NoBrush)

        # Motes drifting up the beam — quicker when the voice is up.
        pts = []
        for x0, ph, sp in self._motes:
            f = (ph + t * (0.10 + 0.25 * sp) * (1.0 + 1.5 * g)) % 1.0
            half = rad0 * (0.55 + 0.70 * f)
            pts.append(QPointF(cx + (x0 * 2.0 - 1.0) * half, base_y - f * col_h * 1.05))
        p.setPen(QPen(_blend(bg, accent, 0.55 + 0.4 * g), 2.0,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawPoints(QPolygonF(pts))

        # ── voice sculpture ─────────────────────────────────────────────────
        hist = np.fromiter(self._hist, dtype=np.float32, count=self._RINGS)
        n, m = self._RINGS, self._SEGS
        hk = np.linspace(0.0, 1.0, n, dtype=np.float32)            # 0 newest .. 1 oldest
        # Radius: a calm vase when silent, swelling with each moment's level,
        # with a few lobes turning around it so loud moments read as shaped.
        lobes = 1.0 + (0.10 + 0.25 * hist[:, None]) * hist[:, None] * np.sin(
            self._a[None, :] * 5.0 + t * 2.6 + hk[:, None] * 4.0)
        base = 0.42 + 0.10 * np.sin(hk * np.pi) + 0.95 * hist
        rad = (base[:, None] * lobes)                                # (n, m)
        X = rad * self._ca[None, :]
        Z = rad * self._sa[None, :]
        Y = np.repeat((hk * 2.0 - 1.0)[:, None], m, axis=1)          # -1 .. +1
        q = np.stack([X, Y, Z], axis=2).reshape(-1, 3) @ R.T
        # Screen: the column stands on the emitter, not about the centre.
        scale = col_h / 2.0
        w = np.maximum(_CAM_D - q[:, 2] / 1.6, 0.4)
        k = (_CAM_D / w)
        xs = (cx + q[:, 0] * k * rad0 * 0.95).reshape(n, m)
        ys = (base_y - col_h * 0.08 - (q[:, 1] + 1.0) * scale * k).reshape(n, m)
        zz = q[:, 2].reshape(n, m)

        fade = (1.0 - hk) ** 1.2                                     # old rings fade
        lines_by_b: list[list[QLineF]] = [[] for _ in range(_BUCKETS)]
        acc_lines: list[QLineF] = []
        for i in range(n):
            front = zz[i] > -0.05
            for j in range(m):
                jj = (j + 1) % m
                a = fade[i] * (0.30 + 0.70 * (front[j] and front[jj]))
                if a < 0.05:
                    continue
                ln = QLineF(float(xs[i, j]), float(ys[i, j]),
                            float(xs[i, jj]), float(ys[i, jj]))
                if i < 3 and hist[i] > 0.06:
                    acc_lines.append(ln)                              # the "now" rings
                else:
                    lines_by_b[min(_BUCKETS - 1, int(a * _BUCKETS))].append(ln)
        step = m // self._MERIDIANS
        for j in range(0, m, step):
            for i in range(n - 1):
                a = fade[i] * (0.45 if zz[i, j] > 0 else 0.15)
                if a < 0.05:
                    continue
                lines_by_b[min(_BUCKETS - 1, int(a * _BUCKETS))].append(
                    QLineF(float(xs[i, j]), float(ys[i, j]),
                           float(xs[i + 1, j]), float(ys[i + 1, j])))
        for b, seg in enumerate(lines_by_b):
            if seg:
                p.setPen(QPen(_blend(bg, primary, (b + 0.6) / _BUCKETS), 1.0))
                p.drawLines(seg)
        if acc_lines:
            p.setPen(QPen(_blend(bg, accent, 0.95), 1.6))
            p.drawLines(acc_lines)

        # ── emitter ─────────────────────────────────────────────────────────
        ew, eh = rad0 * 1.25, rad0 * 0.30
        for k2, (sc, a, wd) in enumerate(((1.00, 0.70, 1.6), (0.78, 0.45, 1.1),
                                          (0.52, 0.85, 2.0))):
            rw, rh = ew * sc, eh * sc
            box = QRectF(cx - rw, base_y - rh, rw * 2, rh * 2)
            p.setPen(QPen(_blend(bg, accent if k2 == 2 else primary,
                                 a * (0.75 + 0.35 * g)), wd))
            p.drawEllipse(box)
            # Dashes turning on the outer rings, opposite ways.
            if k2 < 2:
                p.setPen(QPen(_blend(bg, primary, 0.9), 2.2))
                base_a = (self._spin * (90 if k2 == 0 else -140)) % 360
                for s in range(4):
                    p.drawArc(box, int((base_a + s * 90) * 16), int(26 * 16))
        core = QRadialGradient(cx, base_y, ew * 0.55)
        core.setColorAt(0.0, _c(accent, 200 + 55 * g))
        core.setColorAt(1.0, _c(accent, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(core))
        p.drawEllipse(QRectF(cx - ew * 0.55, base_y - eh * 0.55, ew * 1.1, eh * 1.1))
