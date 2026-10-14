"""
GPU backend for the holographic renderer — same API as `holo_engine.HoloBuffer`.

The numpy engine renders the right picture but its cost grows with the pixels
it covers: a head filling a full-screen HUD (r ≈ 330 px) is about a million
buffer pixels and well over 100 ms a frame. On the GPU the same scene — tens of
thousands of light sprites, anti-aliased lines, a five-level bloom pyramid,
scanlines and optical fringing — costs a few milliseconds at any size.

How it fits the rest of the app
-------------------------------
* Off-screen only. A standalone moderngl context renders into a framebuffer
  and the finished frame is read back as a QImage, which the HUD composites
  with its ordinary QPainter. No QOpenGLWidget, no native child window, so
  overlays and drawers still draw over the HUD (the same trap `QVideoWidget`
  set for the video player).
* Optional. `make_buffer()` returns a GPU buffer when a context can be made,
  otherwise the numpy one, and every renderer is written against the shared
  API — so a machine without OpenGL 3.3 (a VM, a remote desktop) still gets
  the hologram, just at a capped resolution.

Coordinate convention: pixel (0, 0) is the top-left of the buffer. It is mapped
to the bottom of GL's framebuffer, so the rows read back already come out
top-first, exactly as QImage wants them.
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
from PyQt6.QtGui import QColor, QImage

_ctx = None
_ctx_failed = False

_QUAD = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype="f4")      # triangle strip
_SEG = np.array([0, -1, 1, -1, 0, 1, 1, 1], dtype="f4")         # along, across

_SPRITE_VS = """
#version 330
in vec2 a_corner;
in vec2 i_pos; in vec2 i_rad; in float i_val; in float i_layer; in float i_fall;
uniform vec2 u_res;
out vec2 v_uv; out float v_val; flat out int v_layer; out float v_fall;
void main() {
    vec2 p = i_pos + a_corner * i_rad;
    gl_Position = vec4(p / u_res * 2.0 - 1.0, 0.0, 1.0);
    v_uv = a_corner; v_val = i_val; v_layer = int(i_layer + 0.5); v_fall = i_fall;
}
"""
_SPRITE_FS = """
#version 330
in vec2 v_uv; in float v_val; flat in int v_layer; in float v_fall;
out vec4 f;
void main() {
    float a = pow(clamp(1.0 - length(v_uv), 0.0, 1.0), v_fall) * v_val;
    f = v_layer == 0 ? vec4(a, 0, 0, 0) : (v_layer == 1 ? vec4(0, a, 0, 0) : vec4(0, 0, a, 0));
}
"""
# Sprites that carry their own colour (a texture's, not the theme's), drawn
# straight into the RGB "direct" target the surface renderer also uses.
_CSPRITE_VS = """
#version 330
in vec2 a_corner;
in vec2 i_pos; in float i_rad; in vec3 i_rgb; in float i_fall;
uniform vec2 u_res;
out vec2 v_uv; out vec3 v_rgb; out float v_fall;
void main() {
    vec2 p = i_pos + a_corner * i_rad;
    gl_Position = vec4(p / u_res * 2.0 - 1.0, 0.0, 1.0);
    v_uv = a_corner; v_rgb = i_rgb; v_fall = i_fall;
}
"""
_CSPRITE_FS = """
#version 330
in vec2 v_uv; in vec3 v_rgb; in float v_fall;
out vec4 f;
void main() {
    float a = pow(clamp(1.0 - length(v_uv), 0.0, 1.0), v_fall);
    f = vec4(v_rgb * a, 0.0);
}
"""
_LINE_VS = """
#version 330
in vec2 a_corner;
in vec4 i_seg; in float i_val; in float i_layer; in float i_w;
uniform vec2 u_res;
out float v_d; out float v_val; flat out int v_layer; out float v_w;
void main() {
    vec2 p0 = i_seg.xy, p1 = i_seg.zw;
    vec2 d = p1 - p0;
    float L = length(d);
    vec2 t = L > 1e-4 ? d / L : vec2(1.0, 0.0);
    vec2 n = vec2(-t.y, t.x);
    float ext = i_w + 0.75;
    vec2 p = p0 + t * (a_corner.x * L) + n * (a_corner.y * ext);
    gl_Position = vec4(p / u_res * 2.0 - 1.0, 0.0, 1.0);
    v_d = a_corner.y * ext; v_val = i_val; v_layer = int(i_layer + 0.5); v_w = i_w;
}
"""
_LINE_FS = """
#version 330
in float v_d; in float v_val; flat in int v_layer; in float v_w;
out vec4 f;
void main() {
    float a = clamp(1.0 - abs(v_d) / v_w, 0.0, 1.0) * v_val / v_w;
    f = v_layer == 0 ? vec4(a, 0, 0, 0) : (v_layer == 1 ? vec4(0, a, 0, 0) : vec4(0, 0, a, 0));
}
"""
_FULL_VS = """
#version 330
in vec2 a_corner;
out vec2 v_uv;
void main() { v_uv = a_corner * 0.5 + 0.5; gl_Position = vec4(a_corner, 0.0, 1.0); }
"""
# Dual-filter (Kawase) bloom: cheap, wide and free of the box blur's blockiness.
_DOWN_FS = """
#version 330
uniform sampler2D src; uniform vec2 texel;
in vec2 v_uv; out vec4 f;
void main() {
    vec4 s = texture(src, v_uv) * 4.0;
    s += texture(src, v_uv - texel);
    s += texture(src, v_uv + texel);
    s += texture(src, v_uv + vec2(texel.x, -texel.y));
    s += texture(src, v_uv - vec2(texel.x, -texel.y));
    f = s / 8.0;
}
"""
_UP_FS = """
#version 330
uniform sampler2D src; uniform vec2 texel; uniform float gain;
in vec2 v_uv; out vec4 f;
void main() {
    vec4 s = texture(src, v_uv + vec2(-texel.x * 2.0, 0.0));
    s += texture(src, v_uv + vec2(-texel.x, texel.y)) * 2.0;
    s += texture(src, v_uv + vec2(0.0, texel.y * 2.0));
    s += texture(src, v_uv + vec2(texel.x, texel.y)) * 2.0;
    s += texture(src, v_uv + vec2(texel.x * 2.0, 0.0));
    s += texture(src, v_uv + vec2(texel.x, -texel.y)) * 2.0;
    s += texture(src, v_uv + vec2(0.0, -texel.y * 2.0));
    s += texture(src, v_uv + vec2(-texel.x, -texel.y)) * 2.0;
    f = s / 12.0 * gain;
}
"""
_SOFT_FS = """
#version 330
uniform sampler2D src; uniform vec2 texel; uniform vec3 soft;
in vec2 v_uv; out vec4 f;
void main() {
    vec4 c = texture(src, v_uv);
    vec4 b = (texture(src, v_uv + vec2(texel.x, 0)) + texture(src, v_uv - vec2(texel.x, 0))
            + texture(src, v_uv + vec2(0, texel.y)) + texture(src, v_uv - vec2(0, texel.y))
            + c) / 5.0;
    f = vec4(mix(c.rgb, b.rgb, soft), 0.0);
}
"""
# Theme-coloured layers and the direct RGB light become one RGB image here,
# BEFORE the bloom: texture-coloured light then blooms in its own colour.
_COLOR_FS = """
#version 330
uniform sampler2D scene; uniform sampler2D direct;
uniform vec3 c0; uniform vec3 c1; uniform vec3 c2;
in vec2 v_uv; out vec4 f;
void main() {
    vec3 s = texture(scene, v_uv).rgb;
    vec4 d = texture(direct, v_uv);
    f = vec4(s.r * c0 + s.g * c1 + s.b * c2 + d.rgb, d.a);
}
"""
_FINAL_FS = """
#version 330
uniform sampler2D scene; uniform sampler2D bloom;
uniform vec2 res; uniform float t; uniform float exposure; uniform float bloom_amt;
uniform float scan; uniform float ab; uniform vec3 glitch;   // y, height, shift (px)
uniform int tonemap;               // 0: hologram light curve, 1: ACES + sRGB (realistic)
in vec2 v_uv; out vec4 f;
vec3 aces(vec3 x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}
vec3 light(vec2 uv) {
    return texture(scene, uv).rgb + bloom_amt * texture(bloom, uv).rgb;
}
void main() {
    vec2 px = v_uv * res;
    vec2 uv = v_uv;
    if (glitch.y > 0.0 && px.y > glitch.x && px.y < glitch.x + glitch.y)
        uv.x += glitch.z / res.x;
    vec3 col = light(uv);
    if (ab > 0.0) {
        col.r = light(uv + vec2(-ab / res.x, 0.0)).r;
        col.b = light(uv + vec2( ab / res.x, 0.0)).b;
    }
    float rows = 1.0 - scan * (0.5 + 0.5 * cos(px.y * 6.2831853 / 3.0));
    float by = mod(t * 90.0, res.y + 200.0) - 100.0;
    float band = 1.0 + 0.30 * exp(-pow((px.y - by) / 28.0, 2.0));
    col *= rows * band;
    if (tonemap == 1) {
        // A lit character: filmic curve, then encode to sRGB. Alpha is the
        // surface's coverage, or the glow's own brightness, premultiplied.
        vec3 c = pow(aces(col * exposure), vec3(1.0 / 2.2));
        float a = max(texture(scene, uv).a, max(c.r, max(c.g, c.b)));
        f = vec4(c, clamp(a, 0.0, 1.0));
    } else {
        f = vec4(1.0 - exp(-col * exposure), 1.0);
    }
}
"""


def _context():
    """The shared standalone GL context, or None if this machine has none."""
    global _ctx, _ctx_failed
    if _ctx is not None or _ctx_failed:
        return _ctx
    if sys.platform == "win32" and not os.environ.get("MARKLV_IGPU"):
        # Optimus laptops hand python.exe the integrated GPU: on an RTX 3050
        # machine the context came up on "Intel UHD Graphics" and the readback
        # waited 18 ms a frame. This shim flag (read when the NVIDIA OpenGL
        # driver loads, so it works set from here) picks the discrete GPU;
        # AMD/Intel-only machines ignore it. MARKLV_IGPU=1 opts out.
        os.environ.setdefault("SHIM_MCCOMPAT", "0x800000001")
    try:
        import moderngl
        _ctx = moderngl.create_standalone_context(require=330)
        print(f"[HUD] GPU hologram on {_ctx.info.get('GL_RENDERER', '?')}")
    except Exception as e:                 # no moderngl, no driver, a VM…
        print(f"[HUD] GPU hologram unavailable ({e}) — using the software renderer.")
        _ctx_failed = True
        _ctx = None
    return _ctx


def gpu_available() -> bool:
    return _context() is not None


class GpuBuffer:
    """Drop-in for HoloBuffer: same primitives, rendered and post-processed on
    the GPU. Up to three layers (one colour each)."""

    def __init__(self, layers: int = 2, soft: tuple[int, ...] = ()) -> None:
        import moderngl
        self._mgl = moderngl
        self.ctx = _context()
        if self.ctx is None:
            raise RuntimeError("no GL context")
        self.n = min(3, layers)
        self.soft = soft
        self.w = self.h = 0
        ctx = self.ctx
        self._sprite = ctx.program(vertex_shader=_SPRITE_VS, fragment_shader=_SPRITE_FS)
        self._line = ctx.program(vertex_shader=_LINE_VS, fragment_shader=_LINE_FS)
        self._down = ctx.program(vertex_shader=_FULL_VS, fragment_shader=_DOWN_FS)
        self._up = ctx.program(vertex_shader=_FULL_VS, fragment_shader=_UP_FS)
        self._soft_p = ctx.program(vertex_shader=_FULL_VS, fragment_shader=_SOFT_FS)
        self._final = ctx.program(vertex_shader=_FULL_VS, fragment_shader=_FINAL_FS)
        self._color = ctx.program(vertex_shader=_FULL_VS, fragment_shader=_COLOR_FS)
        self._csprite = ctx.program(vertex_shader=_CSPRITE_VS, fragment_shader=_CSPRITE_FS)
        self._quad = ctx.buffer(_QUAD.tobytes())
        self._seg = ctx.buffer(_SEG.tobytes())
        self._full = {p: ctx.vertex_array(p, [(self._quad, "2f", "a_corner")])
                      for p in (self._down, self._up, self._soft_p, self._final,
                                self._color)}
        self._spr: list[np.ndarray] = []
        self._cspr: list[np.ndarray] = []
        self._lin: list[np.ndarray] = []

    # ── targets ─────────────────────────────────────────────────────────────
    def _alloc(self, w: int, h: int) -> None:
        ctx = self.ctx
        for name in ("_scene", "_scene2", "_out", "_col", "_direct"):
            old = getattr(self, name, None)
            if old is not None:
                old[0].release(); old[1].release()
        if getattr(self, "_depth", None) is not None:
            self._depth.release()
        if getattr(self, "_msaa", None) is not None:
            for o in (self._msaa[1], self._msaa[0], self._msaa[2]):
                o.release()
        for lv in getattr(self, "_levels", []):
            lv[0].release(); lv[1].release()

        def target(tw, th, dtype="f2", comps=4):
            tex = ctx.texture((tw, th), comps, dtype=dtype)
            tex.filter = (self._mgl.LINEAR, self._mgl.LINEAR)
            tex.repeat_x = tex.repeat_y = False
            return tex, ctx.framebuffer(color_attachments=[tex])

        self._scene = target(w, h)
        self._scene2 = target(w, h)
        self._out = target(w, h, dtype="f1", comps=4)
        self._col = target(w, h)
        # RGB light that is already coloured — textured surfaces and sprites —
        # with a depth buffer so a surface can hide what is behind it.
        dtex = ctx.texture((w, h), 4, dtype="f2")
        dtex.filter = (self._mgl.LINEAR, self._mgl.LINEAR)
        self._depth = ctx.depth_renderbuffer((w, h))
        self._direct = (dtex, ctx.framebuffer(color_attachments=[dtex],
                                              depth_attachment=self._depth))
        # Surfaces draw into a 4× multisampled copy: a lit, opaque head shows
        # every stair-step on its silhouette that additive light used to hide.
        self._msaa = None
        samples = min(4, getattr(ctx, "max_samples", 0) or 0)
        if samples >= 2:
            try:
                crb = ctx.renderbuffer((w, h), 4, samples=samples, dtype="f2")
                drb = ctx.depth_renderbuffer((w, h), samples=samples)
                self._msaa = (crb, ctx.framebuffer(color_attachments=[crb],
                                                   depth_attachment=drb), drb)
            except Exception:
                self._msaa = None
        self._msaa_used = False
        self._levels = []
        lw, lh = w, h
        for _ in range(5):
            lw, lh = max(1, lw // 2), max(1, lh // 2)
            self._levels.append(target(lw, lh))

    def begin(self, w: int, h: int) -> None:
        w, h = max(8, int(w)), max(8, int(h))
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self._alloc(w, h)
        self._spr.clear()
        self._lin.clear()
        self._cspr.clear()
        self._direct[1].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        if self._msaa is not None:
            self._msaa[1].clear(0.0, 0.0, 0.0, 0.0, depth=1.0)
        self._msaa_used = False

    supports_mesh = True

    @property
    def direct_fbo(self):
        """The RGB + depth target a surface renderer draws into (see
        core.holo_surface); composed with everything else in compose()."""
        if self._msaa is not None:
            self._msaa_used = True
            return self._msaa[1]
        return self._direct[1]

    # ── primitives (HoloBuffer API) ─────────────────────────────────────────
    def points(self, layer: int, xs, ys, val, radius: float = 1.25) -> None:
        """Light sprites carrying `val` total energy each (same as the numpy
        engine's splats), so a scene looks the same on either backend."""
        xs = np.asarray(xs, dtype=np.float32).ravel()
        ys = np.asarray(ys, dtype=np.float32).ravel()
        if xs.size == 0:
            return
        val = np.broadcast_to(np.asarray(val, dtype=np.float32), xs.shape).ravel()
        fall = 1.6
        peak = val * ((fall + 1) * (fall + 2) / (2 * math.pi * radius * radius))
        n = xs.size
        rec = np.empty((n, 7), dtype=np.float32)
        rec[:, 0], rec[:, 1] = xs, ys
        rec[:, 2] = rec[:, 3] = radius
        rec[:, 4], rec[:, 5], rec[:, 6] = peak, layer, fall
        self._spr.append(rec)

    def points_rgb(self, xs, ys, rgb, val, radius: float = 1.25, layer: int = 2) -> None:
        """Sprites in their own colour (`rgb`, n×3 in 0..1) — a model's
        texture instead of the theme. `layer` is the numpy backend's fallback."""
        xs = np.asarray(xs, dtype=np.float32).ravel()
        if xs.size == 0:
            return
        val = np.broadcast_to(np.asarray(val, dtype=np.float32), xs.shape).ravel()
        fall = 1.6
        peak = val * ((fall + 1) * (fall + 2) / (2 * math.pi * radius * radius))
        rec = np.empty((xs.size, 7), dtype=np.float32)
        rec[:, 0], rec[:, 1], rec[:, 2] = xs, np.asarray(ys, np.float32).ravel(), radius
        rec[:, 3:6] = np.asarray(rgb, np.float32) * peak[:, None]
        rec[:, 6] = fall
        self._cspr.append(rec)

    def disc(self, layer: int, cx: float, cy: float, r: float, val: float,
             falloff: float = 2.0, squash: float = 1.0) -> None:
        self._spr.append(np.array([[cx, cy, r, r * squash, val, layer, falloff]],
                                  dtype=np.float32))

    def lines(self, layer: int, x0, y0, x1, y1, val, spacing: float = 0.8,
              width: float = 1.0) -> None:
        x0, y0, x1, y1 = (np.asarray(a, dtype=np.float32).ravel() for a in (x0, y0, x1, y1))
        if x0.size == 0:
            return
        val = np.broadcast_to(np.asarray(val, dtype=np.float32).ravel(), x0.shape)
        rec = np.empty((x0.size, 7), dtype=np.float32)
        rec[:, 0], rec[:, 1], rec[:, 2], rec[:, 3] = x0, y0, x1, y1
        rec[:, 4], rec[:, 5], rec[:, 6] = val, layer, width
        self._lin.append(rec)

    def polyline(self, layer: int, xs, ys, val, closed: bool = False) -> None:
        xs, ys = np.asarray(xs, np.float32), np.asarray(ys, np.float32)
        if closed:
            xs, ys = np.append(xs, xs[0]), np.append(ys, ys[0])
        self.lines(layer, xs[:-1], ys[:-1], xs[1:], ys[1:], val)

    # ── render + post ──────────────────────────────────────────────────────
    def _pass(self, prog, fbo, size, **uniforms) -> None:
        fbo.use()
        self.ctx.viewport = (0, 0) + size
        for k, v in uniforms.items():
            u = prog.get(k, None)          # the compiler drops unused uniforms
            if u is not None:
                u.value = v
        self._full[prog].render(self._mgl.TRIANGLE_STRIP)

    def compose(self, colors: list[QColor], t: float, exposure: float = 1.0,
                bloom: float = 0.9, scan: float = 0.18, aberration: int = 1,
                glitch: float = 0.0, tonemap: int = 0) -> QImage:
        ctx, mgl = self.ctx, self._mgl
        w, h = self.w, self.h
        res = (float(w), float(h))
        if self._msaa_used:
            ctx.copy_framebuffer(self._direct[1], self._msaa[1])

        # 1. Scene: every primitive adds light (ONE, ONE) into a float target.
        scene_tex, scene_fbo = self._scene
        scene_fbo.use()
        ctx.viewport = (0, 0, w, h)
        scene_fbo.clear(0.0, 0.0, 0.0, 0.0)
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.ONE, mgl.ONE
        for prog, recs, geo, layout, names in (
                (self._sprite, self._spr, self._quad, "2f 2f 1f 1f 1f/i",
                 ("i_pos", "i_rad", "i_val", "i_layer", "i_fall")),
                (self._line, self._lin, self._seg, "4f 1f 1f 1f/i",
                 ("i_seg", "i_val", "i_layer", "i_w"))):
            if not recs:
                continue
            data = np.concatenate(recs)
            inst = ctx.buffer(data.tobytes())
            prog["u_res"].value = res
            vao = ctx.vertex_array(prog, [(geo, "2f", "a_corner"), (inst, layout, *names)])
            vao.render(mgl.TRIANGLE_STRIP, instances=len(data))
            vao.release()
            inst.release()
        if self._cspr:
            self._direct[1].use()
            ctx.viewport = (0, 0, w, h)
            data = np.concatenate(self._cspr)
            inst = ctx.buffer(data.tobytes())
            self._csprite["u_res"].value = res
            vao = ctx.vertex_array(self._csprite, [(self._quad, "2f", "a_corner"),
                                                   (inst, "2f 1f 3f 1f/i", "i_pos", "i_rad",
                                                    "i_rgb", "i_fall")])
            vao.render(mgl.TRIANGLE_STRIP, instances=len(data))
            vao.release()
            inst.release()
        ctx.disable(mgl.BLEND)

        # 2. Soften the volume layers (the cloud reads as a body, not static).
        src_tex = scene_tex
        if self.soft:
            soft = tuple(1.0 if i in self.soft else 0.0 for i in range(3))
            scene_tex.use(0)
            self._pass(self._soft_p, self._scene2[1], (w, h), src=0,
                       texel=(1.0 / w, 1.0 / h), soft=soft)
            src_tex = self._scene2[0]

        # 3. Colour: theme layers plus the direct RGB light, one image.
        cols = [(c.redF(), c.greenF(), c.blueF()) for c in colors[:3]]
        while len(cols) < 3:
            cols.append(cols[0])
        src_tex.use(0)
        self._direct[0].use(1)
        self._pass(self._color, self._col[1], (w, h), scene=0, direct=1,
                   c0=cols[0], c1=cols[1], c2=cols[2])
        src_tex = self._col[0]

        # 4. Bloom pyramid: down five levels, then back up, adding as it goes.
        prev = src_tex
        for tex, fbo in self._levels:
            prev.use(0)
            self._pass(self._down, fbo, tex.size, src=0,
                       texel=(1.0 / prev.size[0], 1.0 / prev.size[1]))
            prev = tex
        ctx.enable(mgl.BLEND)
        ctx.blend_func = mgl.ONE, mgl.ONE
        for i in range(len(self._levels) - 1, 0, -1):
            small, (big_tex, big_fbo) = self._levels[i][0], self._levels[i - 1]
            small.use(0)
            self._pass(self._up, big_fbo, big_tex.size, src=0,
                       texel=(0.5 / small.size[0], 0.5 / small.size[1]), gain=1.0)
        ctx.disable(mgl.BLEND)

        # 5. Scanlines, fringing, tone-map.
        gy = gh = gs = 0.0
        if glitch > 0.01:
            gy = (math.sin(t * 13.7) * 0.5 + 0.5) * (h - 20)
            gh = 4 + 10 * glitch
            gs = 6 * glitch * (1 if math.sin(t * 91.0) > 0 else -1)
        src_tex.use(0)
        self._levels[0][0].use(1)
        self._pass(self._final, self._out[1], (w, h), scene=0, bloom=1,
                   res=res, t=float(t),
                   exposure=float(exposure), bloom_amt=float(bloom) * 1.6,
                   tonemap=int(tonemap),
                   scan=float(scan), ab=float(aberration), glitch=(gy, gh, gs))

        raw = self._out[1].read(components=4, alignment=1)
        qi = QImage(raw, w, h, 4 * w, QImage.Format.Format_RGBA8888_Premultiplied)
        qi._keep = raw
        return qi


def make_buffer(layers: int = 2, soft: tuple[int, ...] = ()):
    """A GPU buffer when possible, the numpy one otherwise."""
    if gpu_available():
        try:
            return GpuBuffer(layers, soft)
        except Exception as e:
            print(f"[HUD] GPU buffer failed ({e}) — using the software renderer.")
    from core.holo_engine import HoloBuffer
    return HoloBuffer(layers, soft)
