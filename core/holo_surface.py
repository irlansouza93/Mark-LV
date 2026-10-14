"""
The model's real surface on the GPU — as a game hologram, or as a game
character.

The particle cloud (`core.holo_head`) samples a surface into points; this
renders the surface itself — every triangle of the imported model with its own
texture — in one of two modes (the look's "render"):

  * **Hologram.** The recipe shared by game holograms (Halo's Cortana,
    Cyberpunk's calls): a depth pre-pass so the front hides what is behind it,
    then additive light — Fresnel rim, texture as detail or as colour, drifting
    scan bands, an energy sweep, digital noise; an optional third pass adds the
    hidden surfaces back faintly ("Translucidez").
  * **Realistic.** A lit character, as a game draws one: albedo decoded from
    sRGB, a warm key light, a cool fill, a rim light in the theme's accent,
    sky/ground ambient, wrapped diffuse for skin and a Blinn-Phong highlight;
    opaque, multisampled, tone-mapped with ACES by the buffer. No noise, no
    bands, no glitch.

Both share the rig, which runs in the vertex shader so a 1.6 M-triangle sculpt
costs no numpy per frame and calibrating a model in HOLOGRAMA is live:

  * **Jaw** — everything just below the mouth line turns about the hinge.
  * **Lips** — spread (/i/) and rounded (/u/) from the voice's formants, and
    the upper lip lifting as the mouth opens.
  * **Eyes** — eyeball vertices (tagged by core.mesh_import) turn about their
    fitted centres with the gaze, so saccades are real eye movement.
  * **Lids** — a blink slides the skin above each eye down over it and the
    lower lid up a little, which is what a blink blend-shape does; a model
    with its eyes painted on (a scan) closes the same way.
"""

from __future__ import annotations

import numpy as np

_VS = """
#version 330
in vec3 a_pos; in vec3 a_nrm; in vec2 a_uv; in float a_jaw; in float a_eye; in vec2 a_cav;
uniform mat3 u_rot; uniform vec2 u_oc; uniform float u_r; uniform float u_cam;
uniform vec2 u_res;
uniform int u_rig;                 // 1: weights from the anatomy below, 0: a_jaw
uniform float u_jaw_ang; uniform vec2 u_pivot;     // hinge (y, z)
uniform float u_mouth_y; uniform float u_mouth_z; uniform float u_chin_y;
uniform float u_mouth_w; uniform float u_zfront;
uniform float u_mouth; uniform float u_wide; uniform float u_lips;
uniform vec3 u_eye; uniform vec2 u_eye_rad; uniform float u_blink;
uniform vec3 u_eye_c0; uniform vec3 u_eye_c1; uniform vec2 u_gaze;
uniform float u_eye_r;             // eyeball radius, 0 when the eyes are painted on
uniform vec3 u_glitch;             // band y (model), height, shift
out vec3 v_p; out vec3 v_n; out vec2 v_uv; out vec3 v_rest; out float v_eye; out vec2 v_cav;
out vec3 v_q;                      // posed, before the head turns: where the mouth is now

// 1 below `lo`, 0 above `hi` (GLSL's smoothstep is undefined with its edges
// reversed, so falling ramps are spelled out).
float fall(float lo, float hi, float x) { return 1.0 - smoothstep(lo, hi, x); }
// Without landmarks there is no telling where the lips meet, and a cut in the
// wrong place reads as a second mouth. So no cut: the lower face eases down
// over a quarter of a head-height, the chin moves, nothing tears. Models with
// a face rig (core.face_rig) carry exact per-vertex weights in a_jaw instead.
float jaw_weight(vec3 q) {
    float j = fall(u_mouth_y - 0.25, u_mouth_y + 0.02, q.y);
    j *= clamp(0.3 + 0.85 * (q.z - u_pivot.y) / max(1e-3, u_zfront - u_pivot.y), 0.0, 1.0);
    j *= smoothstep(u_chin_y - 0.30, u_chin_y - 0.05, q.y);
    j *= 1.0 - smoothstep(u_mouth_w * 2.0, u_mouth_w * 3.4, abs(q.x));
    return j;
}
mat3 rot_yx(float yaw, float pitch) {
    float cy = cos(yaw), sy = sin(yaw), cp = cos(pitch), sp = sin(pitch);
    mat3 ry = mat3(cy, 0.0, -sy,  0.0, 1.0, 0.0,  sy, 0.0, cy);
    mat3 rx = mat3(1.0, 0.0, 0.0,  0.0, cp, sp,  0.0, -sp, cp);
    return ry * rx;
}
void main() {
    vec3 q = a_pos;
    vec3 n = a_nrm;

    if (a_eye > 0.5) {
        // Eyeballs turn about their own centres with the gaze.
        vec3 c = a_eye < 1.5 ? u_eye_c0 : u_eye_c1;
        mat3 g = rot_yx(u_gaze.x, u_gaze.y);
        q = c + g * (q - c);
        n = g * n;
    }

    // Lips: spread or round with the vowel, upper lip lifting as it opens.
    if (u_lips > 0.001 && u_mouth > 0.01) {
        vec2 m = vec2(q.x / (u_mouth_w * 1.4), (q.y - u_mouth_y) / 0.10);
        float lw = exp(-dot(m, m)) * smoothstep(u_mouth_z - 0.25, u_mouth_z - 0.08, q.z) * u_lips;
        q.x += lw * u_wide * u_mouth * q.x * 0.30;
        q.z -= lw * u_wide * u_mouth * 0.025;
    }

    float j = u_rig == 1 ? jaw_weight(a_pos) : a_jaw;
    float ang = j * u_jaw_ang;
    if (abs(ang) > 1e-5) {
        float ca = cos(ang), sa = sin(ang);
        float dy = q.y - u_pivot.x, dz = q.z - u_pivot.y;
        q.y = u_pivot.x + dy * ca - dz * sa;
        q.z = u_pivot.y + dy * sa + dz * ca;
        n = vec3(n.x, n.y * ca - n.z * sa, n.y * sa + n.z * ca);
    }

    vec3 p = u_rot * q;
    if (u_glitch.y > 0.0 && abs(p.y - u_glitch.x) < u_glitch.y) p.x += u_glitch.z;
    float w = max(u_cam - p.z, 0.35);
    float k = u_cam * u_r;
    gl_Position = vec4((u_oc.x * w + p.x * k) * 2.0 / u_res.x - w,
                       (u_oc.y * w - p.y * k) * 2.0 / u_res.y - w,
                       -p.z * 0.2 * w, w);
    v_p = p; v_n = u_rot * n; v_uv = a_uv; v_rest = a_pos; v_eye = a_eye; v_cav = a_cav;
    v_q = q;
}
"""

_FS = """
#version 330
in vec3 v_p; in vec3 v_n; in vec2 v_uv; in vec3 v_rest; in float v_eye; in vec2 v_cav;
in vec3 v_q;
uniform float u_seam_x[11]; uniform float u_seam_y[11]; uniform vec2 u_mouth_c;  // (xc, cw)
uniform sampler2D u_tex; uniform int u_has_tex; uniform int u_alpha_test;
uniform vec3 u_factor; uniform vec3 u_pri; uniform vec3 u_acc;
uniform int u_mode;                // 0 hologram, 1 realistic
uniform int u_pass;                // 0 depth, 1 front, 2 hidden (translucency)
uniform float u_cam; uniform float u_t;
uniform float u_tex_mix; uniform float u_tex_detail; uniform float u_tex_contrast;
uniform float u_lum_mid; uniform float u_bright; uniform float u_fill;
uniform float u_rim; uniform float u_rim_pow; uniform float u_rim_acc;
uniform float u_slices; uniform float u_slice_den; uniform float u_slice_spd;
uniform float u_sweep; uniform float u_sweep_y; uniform float u_noise;
uniform float u_flick; uniform float u_bottom; uniform float u_inner;
uniform float u_key; uniform float u_fill_l; uniform float u_spec; uniform float u_sat;
uniform float u_mouth; uniform float u_mouth_y; uniform float u_mouth_w;
uniform float u_blink; uniform vec3 u_lid_rgb;
uniform int u_lids;                // 1: lid curves from a face rig
uniform float u_lid_x0[2]; uniform float u_lid_x1[2]; uniform float u_lid_z[2];
uniform float u_lid_up[18]; uniform float u_lid_lo[18];
uniform int u_iris_on; uniform vec2 u_iris_c[2]; uniform float u_iris_w[2];
uniform vec2 u_gaze_m; uniform float u_r;
out vec4 f;
float curve(int lidset, int e, float t) {
    float x = clamp(t, 0.0, 1.0) * 8.0;
    int i = int(min(floor(x), 7.0));
    float fr = x - float(i);
    int k = e * 9 + i;
    return lidset == 0 ? mix(u_lid_up[k], u_lid_up[k + 1], fr)
                       : mix(u_lid_lo[k], u_lid_lo[k + 1], fr);
}
// Which eye a point is in, and how far inside its opening (x: eye index or
// -1, y: 0..1 inside, z: the upper lid's height there, w: the lower's).
vec4 eye_at(vec3 rest, float is_eye) {
    for (int e = 0; e < 2; e++) {
        float t = (rest.x - u_lid_x0[e]) / max(1e-4, u_lid_x1[e] - u_lid_x0[e]);
        if (t < -0.02 || t > 1.02) continue;
        float up = curve(0, e, t), lo = curve(1, e, t);
        float inside;
        if (is_eye > 0.5) inside = 1.0;          // an eyeball shows only through the opening
        else inside = smoothstep(lo - 0.004, lo + 0.001, rest.y)
                    * (1.0 - smoothstep(up - 0.001, up + 0.004, rest.y))
                    * smoothstep(u_lid_z[e] - 0.10, u_lid_z[e] - 0.05, rest.z);
        return vec4(float(e), inside, up, lo);
    }
    return vec4(-1.0, 0.0, 0.0, 0.0);
}
// Lids, drawn along each eye's own rim (landmarks): the opening is covered
// from the upper lid down, in the skin's colour, with a lash line at the
// edge — a blink that never moves the brows or misses the eye.
// x: covered, y: lash line.
vec2 lid(vec3 rest, float is_eye) {
    if (u_lids == 0 || u_blink < 0.01) return vec2(0.0);
    vec4 e = eye_at(rest, is_eye);
    if (e.x < 0.0 || e.y <= 0.0) return vec2(0.0);
    float edge = mix(e.z + 0.002, e.w - 0.004, u_blink);
    float cover = smoothstep(edge - 0.0015, edge + 0.0015, rest.y) * e.y;
    float lash = exp(-pow((rest.y - edge) / 0.004, 2.0)) * e.y * smoothstep(0.05, 0.3, u_blink);
    return vec2(cover, lash);
}
// Painted eyes look around: inside the opening the texture is read from a
// point shifted against the gaze — fully at the iris, not at all at the rim,
// so the sclera's edge and the lids stay put.
vec2 gaze_uv(vec3 rest, vec2 uv, float is_eye) {
    if (u_iris_on == 0 || is_eye > 0.5) return uv;
    vec4 e = eye_at(rest, 0.0);
    if (e.x < 0.0 || e.y <= 0.0) return uv;
    int i = int(e.x);
    float r = length((rest.xy - u_iris_c[i]) / vec2(u_iris_w[i], u_iris_w[i] * 0.55));
    float m = (1.0 - smoothstep(0.25, 0.75, r)) * e.y;
    // The texture's own slope at this pixel (always the eye's uv island —
    // a map fitted over the whole eye mixed in lash and hair islands and
    // pulled red hair into the iris): model offset → pixels → uv.
    float k = u_cam * u_r / max(0.35, u_cam - v_p.z);
    vec2 off = u_gaze_m * u_iris_w[i] * m * k;          // pixels; window y is model −y
    return uv - (dFdx(uv) * off.x - dFdy(uv) * off.y);
}
float seam_y(float x) {
    float y = u_seam_y[0];
    for (int i = 1; i < 11; i++) {
        if (x > u_seam_x[i - 1]) {
            float t = clamp((x - u_seam_x[i - 1]) / max(1e-4, u_seam_x[i] - u_seam_x[i - 1]), 0.0, 1.0);
            y = mix(u_seam_y[i - 1], u_seam_y[i], t);
        }
    }
    return y;
}
// The inside of an open mouth, by where the fragment is now (not by the
// stretched triangle it belongs to, whose shapes made the teeth a saw):
// upper teeth hanging from the upper lip's line, then the dark of the mouth,
// deepest in the middle, lighter towards the corners.
vec3 mouth_inside(vec3 q, float light) {
    float below = seam_y(q.x) - q.y;
    float ax = abs(q.x - u_mouth_c.x) / max(1e-3, u_mouth_c.y);
    float teeth = (1.0 - smoothstep(0.009, 0.014, below)) * (1.0 - smoothstep(0.55, 0.85, ax));
    vec3 dark = mix(vec3(0.025, 0.007, 0.007), vec3(0.10, 0.028, 0.028), smoothstep(0.2, 0.95, ax));
    return mix(dark, vec3(0.60, 0.58, 0.53) * (0.35 + 0.65 * light), teeth);
}
float hash(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }
void main() {
    vec4 tx = u_has_tex == 1 ? texture(u_tex, gaze_uv(v_rest, v_uv, v_eye)) : vec4(1.0);
    float cut = u_mode == 1 ? 0.5 : 0.35;
    if (u_alpha_test == 1 && tx.a < cut) discard;
    if (u_pass == 0) { f = vec4(0.0); return; }

    vec3 V = normalize(vec3(0.0, 0.0, u_cam) - v_p);
    // Degenerate normals exist in downloaded meshes; one NaN pixel here
    // became a black octagon once the bloom pyramid spread it.
    float nl = length(v_n);
    vec3 N = nl > 1e-6 ? v_n / nl : V;
    float ndv = dot(N, V);
    // Inner walls seen through gaps (an open mouth) are darker; hair cards
    // are double-sided and their backs are as lit as their fronts.
    float facing = (ndv >= 0.0 || u_alpha_test == 1) ? 1.0 : 0.45;
    N = ndv >= 0.0 ? N : -N;
    float fade = clamp((v_rest.y - u_bottom) / 0.45, 0.0, 1.0);
    bool has_col = u_has_tex == 1 || length(u_factor - vec3(1.0)) > 0.05;

    if (u_mode == 1) {
        // ── a lit character ──
        vec3 alb = pow(tx.rgb, vec3(2.2)) * u_factor;          // sRGB → linear
        if (!has_col) alb = vec3(0.26, 0.26, 0.27);             // clay, not chalk
        vec2 lc = lid(v_rest, v_eye);
        if (lc.x > 0.0) {
            alb = mix(alb, pow(u_lid_rgb, vec3(2.2)), lc.x);
            if (v_eye > 0.5) N = normalize(mix(N, V, lc.x * 0.6));   // a lid is not glossy
        }
        alb *= 1.0 - 0.92 * lc.y;
        float g = dot(alb, vec3(0.2126, 0.7152, 0.0722));
        alb = max(mix(vec3(g), alb, u_sat), 0.0);
        vec3 Lk = normalize(vec3(-0.45, 0.50, 0.75));
        vec3 Lf = normalize(vec3(0.70, 0.05, 0.55));
        float wrap = 0.35;                                      // soft skin
        float dk = max(0.0, (dot(N, Lk) + wrap) / (1.0 + wrap));
        float df = max(0.0, (dot(N, Lf) + wrap) / (1.0 + wrap));
        vec3 amb = mix(vec3(0.05, 0.05, 0.06), vec3(0.20, 0.22, 0.26), N.y * 0.5 + 0.5);
        vec3 col = alb * (amb + u_key * vec3(1.00, 0.96, 0.90) * dk
                          + u_fill_l * vec3(0.55, 0.65, 0.85) * df);
        vec3 H = normalize(Lk + V);
        float wet = v_eye > 0.5 ? 1.0 - lc.x : 0.0;            // wet eyes, dry lids
        float shine = mix(42.0, 220.0, wet);
        float sp = pow(max(dot(N, H), 0.0), shine) * (1.0 + 1.5 * wet);
        col += u_spec * 0.35 * sp * vec3(1.0, 0.97, 0.92) * u_key;
        float fres = pow(1.0 - abs(ndv), u_rim_pow);
        col += mix(vec3(1.0), u_acc, u_rim_acc) * fres * u_rim * 0.35;
        col *= facing * facing * u_bright;
        if (v_cav.x > 0.5)
            col = mix(col, mouth_inside(v_q, dk) * u_bright, smoothstep(0.02, 0.10, u_mouth));
        if (any(isnan(col)) || any(isinf(col))) col = vec3(0.0);
        // Premultiplied: the neck's cut fades out instead of ending in a line.
        f = vec4(max(col, vec3(0.0)) * fade, fade);
        return;
    }

    // ── hologram ──
    vec3 alb = tx.rgb * u_factor;
    float lum = dot(alb, vec3(0.2126, 0.7152, 0.0722));
    // Texture as detail: brightness relative to the model's own middle grey,
    // so a dark-skinned or a pale model both land around 1.
    float rel = clamp(0.5 + (lum - u_lum_mid) * u_tex_contrast, 0.0, 1.0) * 2.0;
    float detail = mix(1.0, rel, u_tex_detail);
    float fres = pow(1.0 - abs(ndv), u_rim_pow);
    float key = clamp(dot(N, normalize(vec3(-0.45, 0.55, 0.70))), 0.0, 1.0);

    vec3 theme = u_pri * detail;
    // No texture and a plain white material: there is no "original colour"
    // to show, so it stays in the theme instead of turning into a white statue.
    vec3 orig = has_col ? alb * 1.7 : theme;
    vec3 base = mix(theme, orig, u_tex_mix);
    vec3 col = base * u_fill * (0.30 + 0.70 * key) * facing;
    vec2 lc = lid(v_rest, v_eye);
    col *= 1.0 - 0.45 * lc.x - 0.5 * lc.y;
    col += mix(u_pri, u_acc, u_rim_acc) * fres * u_rim * mix(1.0, detail, 0.35);

    float ph = fract(v_rest.y * u_slice_den - u_t * u_slice_spd);
    col += mix(u_pri, base, 0.5) * exp(-pow((ph - 0.5) / 0.05, 2.0)) * u_slices * 0.55;
    col += u_pri * exp(-pow((v_rest.y - u_sweep_y) / 0.10, 2.0)) * u_sweep;

    if (u_noise > 0.0) {
        // Data blocks: short horizontal cells that dim and re-light, plus a
        // few sparks. Dimmed, not blacked out — black reads as dirt.
        float n = hash(floor(v_rest * vec3(18.0, 70.0, 18.0)) + floor(u_t * 8.0));
        col *= n < u_noise * 0.30 ? 0.45 : 1.0;
        col += u_acc * step(1.0 - u_noise * 0.012, n) * 1.2;
    }
    col *= max(fade, 0.12) * u_flick * u_bright;
    if (v_cav.x > 0.5)
        col = mix(col, u_pri * dot(mouth_inside(v_q, key), vec3(0.33)) * u_bright,
                  smoothstep(0.02, 0.10, u_mouth));
    if (u_pass == 2) col *= u_inner;
    if (any(isnan(col)) || any(isinf(col))) col = vec3(0.0);
    f = vec4(max(col, vec3(0.0)), 0.0);
}
"""


class HoloSurface:
    """One model's surface on the GPU. `gpu` is core.mesh_import's "gpu"
    dict (or an equivalent built from the built-in head)."""

    def __init__(self, ctx, gpu: dict, jaw: np.ndarray | None = None) -> None:
        import moderngl
        self._mgl = moderngl
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=_VS, fragment_shader=_FS)
        pos = np.asarray(gpu["pos"], np.float32)
        n = len(pos)
        uv = np.asarray(gpu.get("uv", np.zeros((n, 2))), np.float32)
        jw = np.zeros(n, np.float32) if jaw is None else np.asarray(jaw, np.float32)
        eye = np.asarray(gpu.get("eye", np.zeros(n)), np.float32)
        cav = np.asarray(gpu.get("cav", np.zeros((n, 2))), np.float32)
        data = np.hstack([np.asarray(gpu["nrm"], np.float32), uv,
                          jw[:, None], eye[:, None], cav])
        # Positions in their own buffer: a model with blend shapes rewrites
        # them every frame it talks or blinks (core.blendshapes).
        self._base = np.ascontiguousarray(pos, np.float32)
        self._pbo = ctx.buffer(self._base.tobytes(), dynamic=True)
        self._vbo = ctx.buffer(np.ascontiguousarray(data, np.float32).tobytes())
        self._ibo = ctx.buffer(np.ascontiguousarray(gpu["idx"], np.uint32).tobytes())
        self.vao = ctx.vertex_array(
            self.prog, [(self._pbo, "3f", "a_pos"),
                        (self._vbo, "3f 2f 1f 1f 2f", "a_nrm", "a_uv", "a_jaw", "a_eye", "a_cav")],
            index_buffer=self._ibo, index_element_size=4)
        from core.blendshapes import from_gpu
        self.shapes = from_gpu(gpu)
        self._last_w: dict = {}
        self.rig = 0 if jaw is not None else 1
        self.has_eyes = bool((eye > 0.5).any())
        self.eye_c = np.asarray(gpu.get("eye_c", np.zeros((2, 3))), np.float32)
        er = np.asarray(gpu.get("eye_r", np.zeros(2)), np.float32)
        self.eye_r = float(er[er > 0].mean()) if (er > 0).any() else 0.0
        self.textures = []
        for t in gpu.get("textures", []):
            t = np.ascontiguousarray(t, np.uint8)
            tex = ctx.texture((t.shape[1], t.shape[0]), 4, t.tobytes())
            tex.build_mipmaps()
            tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
            tex.anisotropy = 8.0
            tex.repeat_x = tex.repeat_y = True
            self.textures.append(tex)
        self.ranges = [(int(r[0]), int(r[1]), int(r[2]), int(r[3]), tuple(r[4:7]))
                       for r in np.asarray(gpu["ranges"])]
        self.n_tris = len(gpu["idx"])

    face = None
    iris_on = False

    def set_face(self, rig: dict | None) -> None:
        """Lid curves and painted-eye gaze from a face rig (core.face_rig)."""
        self.face = rig
        self.iris_on = bool(rig is not None and not self.has_eyes and self.textures)

    def _write_face(self) -> None:
        f, e = self.face, self.face["eyes"]

        def put(name, arr):
            u = self.prog.get(name, None)
            if u is not None:
                u.write(np.ascontiguousarray(arr, np.float32).tobytes())

        put("u_lid_x0", [e[0]["x0"], e[1]["x0"]])
        put("u_lid_x1", [e[0]["x1"], e[1]["x1"]])
        put("u_lid_z", [e[0]["z"], e[1]["z"]])
        put("u_lid_up", np.r_[e[0]["up"], e[1]["up"]])
        put("u_lid_lo", np.r_[e[0]["lo"], e[1]["lo"]])
        put("u_iris_c", np.r_[e[0]["iris"], e[1]["iris"]])
        put("u_iris_w", [e[0]["w"], e[1]["w"]])
        sm = f["seam"]
        idx = np.linspace(0, len(sm) - 1, 11).round().astype(int)
        put("u_seam_x", sm[idx, 0])
        put("u_seam_y", sm[idx, 1])
        self._set(u_mouth_c=(float(f["xc"]), float(f["cw"])))
        self._set(u_lid_rgb=tuple(float(c) for c in f["skin"]))

    def release(self) -> None:
        for o in [self.vao, self._vbo, self._pbo, self._ibo, self.prog, *self.textures]:
            try:
                o.release()
            except Exception:
                pass

    def _set(self, **u) -> None:
        for k, v in u.items():
            m = self.prog.get(k, None)
            if m is not None:
                m.value = v

    def draw(self, fbo, w: int, h: int, *, rot, oc, r: float, cam: float,
             t: float, look: dict, primary, accent, jaw_ang: float,
             pivot, zfront: float, bottom: float, sweep_y: float, flick: float,
             lum_mid: float, blink: float, mouth: float = 0.0, wide: float = 0.0,
             mouth_z: float = 0.5, gaze=(0.0, 0.0), glitch=(0.0, 0.0, 0.0)) -> None:
        mgl, ctx = self._mgl, self.ctx
        real = look.get("render") == "realista"
        sh = self.shapes
        if sh is not None:
            mw = sh.weights(mouth, wide, blink * float(look["blink"]),
                            gaze if sh.has_gaze else (0.0, 0.0), gain=float(look["jaw"]))
            mw = {i: round(v, 3) for i, v in mw.items()}
            if mw != self._last_w:
                self._pbo.write(sh.blend(self._base, mw).tobytes())
                self._last_w = mw
            # What the shapes do, the procedural rig does not do again.
            if sh.has_mouth:
                jaw_ang = 0.0
            if sh.has_blink:
                blink = 0.0
            if sh.has_gaze:
                gaze = (0.0, 0.0)
        amt = 1.0 if real else float(look.get("surface", 0.0))
        size = float(look.get("eye_size", 1.0))
        gz = float(look.get("gaze", 1.0))
        self._set(u_rot=tuple(np.asarray(rot, np.float32).T.ravel()), u_oc=tuple(oc),
                  u_r=float(r), u_cam=float(cam), u_res=(float(w), float(h)),
                  u_rig=self.rig, u_jaw_ang=float(jaw_ang), u_pivot=tuple(pivot),
                  u_mouth_y=float(look["mouth_y"]), u_mouth_z=float(mouth_z),
                  u_chin_y=float(look["chin_y"]), u_mouth_w=float(look["mouth_w"]),
                  u_zfront=float(zfront), u_mouth=float(mouth), u_wide=float(wide),
                  u_lips=float(look.get("lips", 1.0)) if (self.rig == 1 or self.face is not None) else 0.0,

                  u_lid_rgb=tuple(float(look.get(k, d)) for k, d in
                                  (("lid_r", 0.75), ("lid_g", 0.62), ("lid_b", 0.55))),
                  u_blink=float(blink) * float(look["blink"]),
                  u_eye_c0=tuple(float(x) for x in self.eye_c[0]),
                  u_eye_c1=tuple(float(x) for x in self.eye_c[1]),
                  # ±20° across, ±13° up and down at full gaze — a saccade, not a roll
                  u_gaze=(float(gaze[0]) * 0.35 * gz, -float(gaze[1]) * 0.22 * gz),
                  u_glitch=tuple(glitch), u_t=float(t), u_mode=int(real),
                  u_pri=(primary.redF(), primary.greenF(), primary.blueF()),
                  u_acc=(accent.redF(), accent.greenF(), accent.blueF()),
                  u_tex_mix=float(look["tex_mix"]), u_tex_detail=float(look["tex_detail"]),
                  u_tex_contrast=float(look["tex_contrast"]), u_lum_mid=float(lum_mid),
                  u_bright=float(look["brightness"]) * amt, u_fill=float(look["fill"]),
                  u_rim=float(look["rim"]), u_rim_pow=float(look["rim_power"]),
                  u_rim_acc=float(look["rim_accent"]), u_slices=float(look["slices"]),
                  u_slice_den=float(look["slice_density"]),
                  u_slice_spd=float(look["slice_speed"]) / max(1.0, float(look["slice_density"])) * 14.0,
                  u_sweep=float(look["sweep"]), u_sweep_y=float(sweep_y),
                  u_noise=float(look["noise"]), u_flick=float(flick),
                  u_bottom=float(bottom), u_inner=float(look["inner"]),
                  u_key=float(look.get("key_light", 1.0)),
                  u_fill_l=float(look.get("fill_light", 0.5)),
                  u_spec=float(look.get("specular", 0.6)),
                  u_sat=float(look.get("saturation", 1.0)), u_tex=0,
                  u_lids=int(self.face is not None), u_iris_on=int(self.iris_on),
                  # Gaze as a model-space offset of the iris, in eye half-widths.
                  # (A third of the eye's half-width reached past the iris
                  # into the lashes and drew a dark ring.)
                  u_gaze_m=(-float(gaze[0]) * 0.20 * gz, float(gaze[1]) * 0.10 * gz))
        if self.face is not None:
            self._write_face()

        fbo.use()
        ctx.viewport = (0, 0, w, h)
        ctx.enable(mgl.DEPTH_TEST)
        if real:
            # Opaque: one depth-tested pass writes the nearest surface.
            passes = [(1, "<", False)]
        else:
            passes = [(0, "<", False), (1, "<=", True)]
            if float(look["inner"]) > 0.005:
                passes.append((2, ">", True))
        for pss, func, blend in passes:
            ctx.depth_func = func
            fbo.color_mask = (pss != 0,) * 4
            fbo.depth_mask = not blend
            if blend:
                ctx.enable(mgl.BLEND)
                ctx.blend_func = mgl.ONE, mgl.ONE
            else:
                ctx.disable(mgl.BLEND)
            self._set(u_pass=pss)
            for first, count, ti, alpha_test, factor in self.ranges:
                has = 0 <= ti < len(self.textures)
                if has:
                    self.textures[ti].use(0)
                self._set(u_has_tex=int(has), u_alpha_test=int(has and alpha_test),
                          u_factor=factor)
                self.vao.render(mgl.TRIANGLES, vertices=count, first=first)
        fbo.color_mask = (True, True, True, True)
        fbo.depth_mask = True
        ctx.disable(mgl.DEPTH_TEST)
        ctx.disable(mgl.BLEND)
