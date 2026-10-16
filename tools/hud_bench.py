"""Offscreen HUD check: render the centre of the HUD, save a frame, time it.

CLAUDE.md asks every HUD change to be looked at and measured (budget < 15 ms per
frame at r≈330 px, i.e. a ~700 px canvas). This does both without opening a
window, so it also runs in CI-like sessions:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tools/hud_bench.py \
        --style holo --size 700 --frames 60 --out hud.png

Timing covers _step() (animation) plus grab() (paint into a pixmap); grab() adds
a copy the real window does not pay, so the number is a slight overestimate —
the safe side for a budget. Check GL_RENDERER in the log before trusting a GPU
number on an Optimus laptop (core/holo_gpu.py).
"""
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The offscreen platform ships no font database on Windows: every label renders
# as empty boxes unless Qt is pointed at the system fonts.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR",
                          os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--style", default="", help="HUD style key (default: the saved one)")
    ap.add_argument("--size", type=int, default=700, help="canvas side in px")
    ap.add_argument("--frames", type=int, default=60, help="timed frames")
    ap.add_argument("--warmup", type=int, default=10, help="untimed frames first")
    ap.add_argument("--speaking", action="store_true", help="animate as if talking")
    ap.add_argument("--out", default="", help="save the last frame as PNG")
    args = ap.parse_args()

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    import ui

    hud = ui.HudCanvas("face.png")
    hud._tmr.stop()                 # we drive the frames ourselves
    hud.resize(args.size, args.size)
    if args.style:
        hud.set_style(args.style)
    hud.speaking = args.speaking

    def frame():
        hud._step()
        return hud.grab()

    for _ in range(args.warmup):
        frame()
        app.processEvents()

    times, pix = [], None
    for _ in range(args.frames):
        t0 = time.perf_counter()
        pix = frame()
        times.append((time.perf_counter() - t0) * 1000.0)
        app.processEvents()

    times.sort()
    p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
    print(f"style={args.style or '(saved)'} size={args.size} frames={len(times)} "
          f"median={statistics.median(times):.1f}ms p95={p95:.1f}ms max={times[-1]:.1f}ms "
          f"budget=15ms {'OK' if p95 < 15 else 'OVER'}")
    if args.out and pix is not None:
        pix.save(args.out)
        print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
