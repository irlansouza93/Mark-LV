"""
Screen & webcam capture for JARVIS vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

try:
    import cv2
    _CV2 = True
    # Probing indices that do not exist is how detection works, and OpenCV
    # prints a multi-line error for every one of them. The outcome is reported
    # here in one line instead.
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        pass
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_screen() -> tuple[bytes, str]:

    if not _MSS:
        raise RuntimeError("mss is not installed. Run: pip install mss")

    with mss.mss() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        target   = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)
        png      = mss.tools.to_png(shot.rgb, shot.size)

    return _compress(png, "PNG")


def _cv2_backend() -> int:
    """Return the preferred OpenCV camera backend for the current OS."""
    return _cv2_backends()[0] if _CV2 else 0


def _cv2_backends() -> list[int]:
    """Backends to try, best first. On Windows a camera that DirectShow cannot
    open is often fine under Media Foundation (and vice versa), so both are
    tried before giving up on an index."""
    if not _CV2:
        return [0]
    os_name = _get_os()
    if os_name == "windows":
        return [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    if os_name == "mac":
        return [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
    return [cv2.CAP_V4L2, cv2.CAP_ANY] if hasattr(cv2, "CAP_V4L2") else [cv2.CAP_ANY]


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    # Rejects all-black frames (an IR sensor, a closed privacy shutter), not
    # dim ones: the old threshold of 8 also rejected a real camera in an
    # unlit room and then saved index 0 as if it had been found.
    return bool(np.mean(frame) > 1.5)


def camera_problem() -> str:
    """Why the camera cannot be opened, when the OS says so; "" if unknown.

    The commonest cause on Windows is not a driver or an index at all: camera
    access is switched off in Settings → Privacy & security → Camera, and
    OpenCV then fails to open every device without saying why. Reading the
    consent store (read-only) turns that into a sentence the user can act on.
    """
    if _get_os() != "windows":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"

    def _val(root, sub=""):
        try:
            with winreg.OpenKey(root, base + (("\\" + sub) if sub else "")) as k:
                return str(winreg.QueryValueEx(k, "Value")[0])
        except OSError:
            return ""

    if _val(winreg.HKEY_LOCAL_MACHINE) == "Deny":
        return ("Camera access is turned OFF for this device in Windows. Open "
                "Settings → Privacy & security → Camera and turn on 'Camera "
                "access' and 'Let desktop apps access your camera'.")
    if _val(winreg.HKEY_CURRENT_USER) == "Deny":
        return ("Camera access is turned off for this user in Windows. Open "
                "Settings → Privacy & security → Camera and turn on 'Camera access'.")
    if _val(winreg.HKEY_CURRENT_USER, "NonPackaged") == "Deny":
        return ("Windows blocks desktop apps from the camera. Open Settings → "
                "Privacy & security → Camera and turn on 'Let desktop apps "
                "access your camera'.")
    return ""


def _detect_camera_index() -> int | None:
    """First index that delivers a usable frame on any backend, or None.

    Only a real find is saved. Saving 0 after a failed search — what this used
    to do — pinned a guess into the config forever, so a camera plugged in (or
    permitted) later was never looked for again.
    """
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        for backend in _cv2_backends():
            if _probe_camera(idx, backend):
                print(f"[Vision] ✅ Camera found at index {idx}")
                _save_config_key("camera_index", idx)
                return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")
    print("[Vision] ⚠️  No camera found")
    return None


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    idx = _detect_camera_index()
    return 0 if idx is None else idx


def open_camera(width: int = 1280, height: int = 720):
    """An opened, warmed-up cv2.VideoCapture — or raise with the real reason.

    Tries the saved index on every backend, then searches again from scratch
    (the saved index may be stale: a USB camera moved, a virtual camera added).
    The single place the app opens the webcam, so the snapshot tool and the
    live stream can never disagree about which camera or backend to use.
    """
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    def _try(idx: int):
        for backend in _cv2_backends():
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                ok = False
                for _ in range(8):
                    ret, frame = cap.read()
                    ok = ok or (ret and frame is not None)
                if ok:
                    return cap
            cap.release()
        return None

    cap = _try(_get_camera_index())
    if cap is None:
        idx = _detect_camera_index()
        if idx is not None:
            cap = _try(idx)
    if cap is None:
        why = camera_problem()
        raise RuntimeError(why or "No webcam could be opened — check that one is "
                                  "connected and not in use by another app.")
    return cap


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    cap = open_camera()
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera returned no frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"
