"""
Holographic voice visualisers for the HUD centre — the two non-face styles.

Both are drawn as light through `core.holo_engine` / `core.holo_gpu` (additive
sprites and lines, a bloom pyramid, scanlines, optical fringing — see those
modules for why), and both follow the rules the avatar and the reactor set:

* **Everything moves for a reason.** Spin is the assistant's state (idle
  drifts, thinking hurries, speaking runs); size, ripple and brightness are the
  real audio level the HUD already smooths for its waveform.
* **Theme-agnostic.** Colours arrive as arguments, so the hue wheel retints.

Shared lifecycle with `HoloAvatar`:
    viz.step(dt, amp, speaking=..., muted=..., state=...)   # every tick
    viz.paint(painter, cx, cy, r, primary, accent, bg, W, H)

`HoloOrb`        — an energy sphere of light points with a bright limb, a hot
                   core, orbit rings carrying comets, and a spectrum ring that
                   is the voice.
`HoloProjector`  — an emitter casting a beam, and in it a sculpture of the
                   voice's recent history: one ring per moment, newest at the
                   emitter, rising and fading as it ages.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np
from PyQt6.QtGui import QColor, QPainter

from core.holo_engine import budget_scale, draw_additive
from core.holo_gpu import make_buffer

_CAM_D = 4.2
_PRI, _ACC = 0, 1


def _rot(yaw: float, pitch: float) -> np.ndarray:
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    return np.array([[cy, 0.0, sy],
                     [sp * sy, cp, -sp * cy],
                     [-cp * sy, sp, cp * cy]], dtype=np.float32)


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
        self._buf = make_buffer(2)

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
        # phase (the lesson the head's sway and the core's rings taught).
        rate = 0.15 if asleep else (0.30 + (0.9 if thinking else 0.0)
                                    + (0.40 if speaking else 0.0))
        self._spin += dt * rate
        self._advance(dt, amp)

    def _advance(self, dt: float, amp: float) -> None:
        pass

    def _finish(self, p: QPainter, x0: float, y0: float, primary: QColor,
                accent: QColor, sc: float, exposure: float = 1.0,
                bloom: float = 0.5) -> None:
        img = self._buf.compose([primary, accent], self._t, exposure=exposure,
                                bloom=bloom, scan=0.14, aberration=1)
        draw_additive(p, img, x0, y0, sc)


class HoloOrb(_Base):
    """An energy sphere that breathes with the voice."""

    def __init__(self, n: int = 5200) -> None:
        super().__init__()
        i = np.arange(n, dtype=np.float32) + 0.5
        phi = np.arccos(1.0 - 2.0 * i / n)
        theta = np.pi * (1.0 + 5.0 ** 0.5) * i
        self._p0 = np.stack([np.cos(theta) * np.sin(phi), np.cos(phi),
                             np.sin(theta) * np.sin(phi)], axis=1).astype(np.float32)
        self._lat = phi.astype(np.float32)
        self._lon = (theta % (2 * np.pi)).astype(np.float32)
        self._jit = np.random.default_rng(2).random(n).astype(np.float32)
        a = np.linspace(0.0, 2 * np.pi, 160, endpoint=False, dtype=np.float32)
        self._circle = np.stack([np.cos(a), np.zeros_like(a), np.sin(a)], axis=1)
        # radius, tilt, yaw offset, speed — three orbits at distinct angles.
        self._rings = ((1.32, 0.42, 0.0, 1.0), (1.50, -0.30, 1.2, -0.7),
                       (1.72, 1.12, 2.1, 0.45))
        rng = np.random.default_rng(9)
        self._motes = rng.random((260, 4)).astype(np.float32)

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None,
              W: float = 0.0, H: float = 0.0) -> None:
        t, g = self._t, self._glow
        x0, y0 = cx - r, cy - r
        buf = self._buf
        sc = budget_scale(buf, r, cap=200.0)
        r = r * sc
        size = int(2.0 * r)
        buf.begin(size, size)
        ox = oy = r                      # orb centre inside the buffer
        rs = r * 0.40                    # sphere radius in px
        R = _rot(self._spin, 0.36 + 0.08 * math.sin(t * 0.21))

        def proj(q, scale):
            w = np.maximum(_CAM_D - q[:, 2], 0.4)
            k = (_CAM_D / w) * scale
            return ox + q[:, 0] * k, oy - q[:, 1] * k

        # ── sphere: displaced by the voice, bright at the limb ──────────────
        wave = (np.sin(self._lat * 5.0 + t * 2.4) * np.cos(self._lon * 3.0 - t * 1.7)
                + 0.6 * np.sin(self._lat * 9.0 - t * 3.1 + self._lon * 2.0))
        rad = 1.0 + 0.012 * math.sin(t * 1.3) + (0.05 + 0.20 * g) * g * wave
        q = (self._p0 * rad[:, None]) @ R.T
        xs, ys = proj(q, rs)
        nz = q[:, 2] / np.maximum(np.linalg.norm(q, axis=1), 1e-6)
        limb = np.clip(1.0 - np.abs(nz), 0.0, 1.0) ** 2.2
        flick = 0.85 + 0.15 * np.sin(t * 29.0 + self._jit * 50.0)
        val = (np.where(nz > 0, 0.20, 0.07) + 0.95 * limb) * flick * (0.8 + 0.6 * g)
        density = rs * rs / len(xs) * 5.0
        buf.points(_PRI, xs, ys, val * density, radius=max(1.2, r / 220.0))

        # Latitude bands — the structure that makes it a globe, not a blob.
        for lat in (-0.6, -0.3, 0.0, 0.3, 0.6):
            ring = self._circle * np.array([math.cos(math.asin(lat)), 0, math.cos(math.asin(lat))]) \
                + np.array([0.0, lat, 0.0])
            qq = ring @ R.T
            bx, by = proj(qq, rs * (1.0 + 0.04 * g))
            front = 0.5 * (qq[:-1, 2] + qq[1:, 2]) > 0
            v = np.where(front, 0.28, 0.07)
            buf.lines(_PRI, bx[:-1], by[:-1], bx[1:], by[1:], v)

        # ── hot core ───────────────────────────────────────────────────────
        buf.disc(_ACC, ox, oy, rs * 0.95, 0.12 + 0.30 * g, 2.6)
        buf.disc(_ACC, ox, oy, rs * 0.24, 0.55 + 0.70 * g, 1.8)

        # ── orbits with comets ─────────────────────────────────────────────
        for k, (rr, tilt, yaw0, spd) in enumerate(self._rings):
            M = R @ _rot(yaw0 + self._spin * spd * 0.6, tilt)
            qq = (self._circle * rr) @ M.T
            bx, by = proj(qq, rs)
            z = 0.5 * (qq[:-1, 2] + qq[1:, 2])
            buf.lines(_PRI, bx[:-1], by[:-1], bx[1:], by[1:], np.where(z > 0, 0.40, 0.12))
            n = len(bx)
            head = int((t * 22.0 * spd) % n) if spd > 0 else int((-t * 22.0 * -spd) % n)
            tail = (head - np.arange(26) * (1 if spd > 0 else -1)) % n
            fall = np.linspace(1.0, 0.0, 26) ** 1.6
            buf.points(_ACC, bx[tail], by[tail], fall * 2.2, radius=1.6)
            buf.disc(_ACC, float(bx[head]), float(by[head]), 5.0, 1.6, 1.5)

        # ── the voice: a spectrum ring in the screen plane ─────────────────
        m = 120
        a = np.linspace(0, 2 * np.pi, m, endpoint=False)
        shape = (0.55 + 0.45 * np.sin(a * 3.0 + t * 1.9) * np.sin(a * 5.0 - t * 2.7))
        live = g if not self._muted else 0.0
        h = r * (0.012 + 0.16 * live * np.abs(shape) + 0.006 * np.sin(a * 9 + t * 4))
        r0 = rs * 1.12
        buf.lines(_ACC if live > 0.05 else _PRI,
                  ox + np.cos(a) * r0, oy + np.sin(a) * r0,
                  ox + np.cos(a) * (r0 + h), oy + np.sin(a) * (r0 + h),
                  0.35 + 0.9 * live, width=1.4)

        # ── frame: a thin dial that turns slowly ───────────────────────────
        a = np.linspace(0, 2 * np.pi, 361)
        rf = r * 0.93
        buf.polyline(_PRI, ox + np.cos(a) * rf, oy + np.sin(a) * rf, 0.16)
        ticks = np.arange(72) * (2 * np.pi / 72) + self._spin * 0.15
        ln = np.where(np.arange(72) % 6 == 0, 0.035, 0.015) * r
        buf.lines(_PRI, ox + np.cos(ticks) * rf, oy + np.sin(ticks) * rf,
                  ox + np.cos(ticks) * (rf - ln), oy + np.sin(ticks) * (rf - ln), 0.30)

        # ── motes drifting around the orb ──────────────────────────────────
        mo = self._motes
        ang = mo[:, 0] * 2 * np.pi + t * (0.15 + 0.35 * mo[:, 1]) * (1 + g)
        dist = rs * (1.25 + 1.0 * mo[:, 2])
        buf.points(_PRI, ox + np.cos(ang) * dist, oy + np.sin(ang) * dist * 0.92,
                   0.6 * (0.4 + 0.6 * np.sin(t * 2 + mo[:, 3] * 6) ** 2), radius=1.3)

        self._finish(p, x0, y0, primary, accent, sc, exposure=0.85, bloom=0.45)


class HoloProjector(_Base):
    """A holographic projector rendering the voice as a rising sculpture."""

    _RINGS = 36          # history depth: one ring per sample
    _SEGS = 72           # points around each ring
    _MERIDIANS = 18      # vertical wires tying the rings into a surface
    _HOP = 0.055         # seconds between history samples

    def __init__(self) -> None:
        super().__init__()
        self._hist = deque([0.0] * self._RINGS, maxlen=self._RINGS)
        self._acc = 0.0
        self._peak = 0.0
        a = np.linspace(0.0, 2 * np.pi, self._SEGS, endpoint=False, dtype=np.float32)
        self._ca, self._sa, self._a = np.cos(a), np.sin(a), a
        rng = np.random.default_rng(11)
        self._motes = rng.random((140, 3)).astype(np.float32)
        self._beam = rng.random((70, 2)).astype(np.float32)

    def _advance(self, dt: float, amp: float) -> None:
        # Sample at a fixed rate so the sculpture's height is a fixed span of
        # time at any paint rate; keep the peak so no syllable is missed.
        self._peak = max(self._peak, amp)
        self._acc += dt
        while self._acc >= self._HOP:
            self._acc -= self._HOP
            self._hist.appendleft(self._peak)
            self._peak = 0.0

    def paint(self, p: QPainter, cx: float, cy: float, r: float,
              primary: QColor, accent: QColor, bg: QColor | None = None,
              W: float = 0.0, H: float = 0.0) -> None:
        t, g = self._t, self._glow
        x0, y0 = cx - r, cy - r
        buf = self._buf
        sc = budget_scale(buf, r, cap=200.0)
        r = r * sc
        bw, bh = int(2.0 * r), int(2.05 * r)
        buf.begin(bw, bh)
        ox = r
        base_y = r * 1.42                       # emitter centre inside the buffer
        col_h = r * 1.12
        rad0 = r * 0.50

        # ── beam: faint rays from the emitter, widening upward ─────────────
        top_y = base_y - col_h * 1.08
        bx0 = (self._beam[:, 0] * 2 - 1) * rad0 * 0.52
        bx1 = bx0 * 2.3
        flick = 0.5 + 0.5 * np.sin(t * (2 + 3 * self._beam[:, 1]) + self._beam[:, 1] * 9)
        # Each ray in segments that fade with height — a beam that dissolves
        # into the air, not one cut off by the edge of its buffer.
        for s in range(8):
            f0, f1 = s / 8.0, (s + 1) / 8.0
            buf.lines(_PRI, ox + bx0 + (bx1 - bx0) * f0, np.full_like(bx0, base_y + (top_y - base_y) * f0),
                      ox + bx0 + (bx1 - bx0) * f1, np.full_like(bx0, base_y + (top_y - base_y) * f1),
                      (0.035 + 0.05 * g) * flick * (1.0 - f0) ** 1.5, width=2.5)
        buf.disc(_PRI, ox, base_y - col_h * 0.45, rad0 * 1.3, 0.10 + 0.10 * g, 2.0, squash=1.4)

        mo = self._motes
        f = (mo[:, 1] + t * (0.08 + 0.22 * mo[:, 2]) * (1.0 + 1.5 * g)) % 1.0
        half = rad0 * (0.5 + 0.75 * f)
        buf.points(_ACC, ox + (mo[:, 0] * 2 - 1) * half, base_y - f * col_h * 1.05,
                   (1.0 - f) * (0.8 + 0.8 * g), radius=1.4)

        # ── voice sculpture ─────────────────────────────────────────────────
        hist = np.fromiter(self._hist, dtype=np.float32, count=self._RINGS)
        n, m = self._RINGS, self._SEGS
        hk = np.linspace(0.0, 1.0, n, dtype=np.float32)              # 0 newest .. 1 oldest
        lobes = 1.0 + (0.10 + 0.25 * hist[:, None]) * hist[:, None] * np.sin(
            self._a[None, :] * 5.0 + t * 2.6 + hk[:, None] * 4.0)
        base = 0.40 + 0.10 * np.sin(hk * np.pi) + 0.95 * hist
        rad = base[:, None] * lobes
        X, Z = rad * self._ca[None, :], rad * self._sa[None, :]
        Y = np.repeat((hk * 2.0 - 1.0)[:, None], m, axis=1)
        R = _rot(self._spin * 0.9, 0.40)
        q = np.stack([X, Y, Z], axis=2).reshape(-1, 3) @ R.T
        w = np.maximum(_CAM_D - q[:, 2] / 1.6, 0.4)
        k = _CAM_D / w
        xs = (ox + q[:, 0] * k * rad0 * 0.95).reshape(n, m)
        ys = (base_y - col_h * 0.08 - (q[:, 1] + 1.0) * (col_h / 2.0) * k).reshape(n, m)
        zz = q[:, 2].reshape(n, m)

        fade = (1.0 - hk) ** 1.15
        nxt = np.roll(np.arange(m), -1)
        front = (zz + zz[:, nxt]) > 0
        val = fade[:, None] * np.where(front, 0.55, 0.14)
        cur = (np.arange(n) < 3)[:, None] & (hist[:, None] > 0.06)
        for layer, mask in ((_PRI, ~cur), (_ACC, np.broadcast_to(cur, val.shape))):
            sel = mask & (val > 0.02)
            if sel.any():
                ii, jj = np.nonzero(sel)
                buf.lines(layer, xs[ii, jj], ys[ii, jj], xs[ii, nxt[jj]], ys[ii, nxt[jj]],
                          val[ii, jj] * (1.5 if layer == _ACC else 1.0))
        js = np.arange(0, m, m // self._MERIDIANS)
        ii = np.repeat(np.arange(n - 1), len(js))
        jj = np.tile(js, n - 1)
        mv = fade[ii] * np.where(zz[ii, jj] > 0, 0.30, 0.08)
        buf.lines(_PRI, xs[ii, jj], ys[ii, jj], xs[ii + 1, jj], ys[ii + 1, jj], mv)

        # ── emitter ────────────────────────────────────────────────────────
        a = np.linspace(0, 2 * np.pi, 200, endpoint=False)
        ew, eh = rad0 * 1.30, rad0 * 0.28
        for sc, v, layer in ((1.00, 0.75, _PRI), (0.80, 0.40, _PRI), (0.55, 0.9, _ACC)):
            buf.polyline(layer, ox + np.cos(a) * ew * sc, base_y + np.sin(a) * eh * sc,
                         v * (0.8 + 0.4 * g), closed=True)
        for s, (sc, spd) in enumerate(((1.0, 0.9), (0.80, -1.4))):
            for d in range(4):
                aa = np.linspace(0, 0.45, 18) + self._spin * spd + d * np.pi / 2
                buf.polyline(_PRI, ox + np.cos(aa) * ew * sc, base_y + np.sin(aa) * eh * sc, 1.1)
        buf.disc(_ACC, ox, base_y, ew * 0.50, 0.35 + 0.45 * g, 2.2, squash=eh / ew)

        self._finish(p, x0, y0, primary, accent, sc, exposure=0.85, bloom=0.45)
