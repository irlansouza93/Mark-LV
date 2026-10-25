"""video_player, browser_control and computer_control obey core/fs_policy (spec 003, T022)."""
import sys

import pytest

from core import fs_policy

MARK = "[FOLDER_POLICY]"


@pytest.fixture
def env(tmp_path):
    d = {k: tmp_path / k for k in ("home", "app", "rw", "outside")}
    for p in d.values():
        p.mkdir()
    fs_policy.configure(folders=[(d["rw"], "rw")], home=d["home"], app_dir=d["app"], env={},
                        platform=sys.platform, uploaded=None)
    yield d
    fs_policy.reset()


class FakePlayer:
    def __init__(self):
        self.shown = []

    def show_video(self, src, title, **kw):
        self.shown.append(src)

    def video_is_playing(self):
        return False


def test_video_local_outside_is_refused(env):
    from actions import video_player as vp
    f = env["outside"] / "v.mp4"
    f.write_bytes(b"x")
    pl = FakePlayer()
    assert vp.video_player({"source": str(f)}, player=pl).startswith(MARK)
    assert pl.shown == []


def test_video_local_allowed_plays_and_url_skips_policy(env):
    from actions import video_player as vp
    f = env["rw"] / "v.mp4"
    f.write_bytes(b"x")
    pl = FakePlayer()
    assert vp.video_player({"source": str(f)}, player=pl).startswith("Playing")
    assert vp.video_player({"source": "https://example.com/a.mp4"}, player=pl).startswith("Playing")
    assert len(pl.shown) == 2


def test_computer_control_screenshot_outside_is_refused_and_writes_nothing(env, monkeypatch):
    cc = pytest.importorskip("actions.computer_control")
    shots = []

    class Img:
        def save(self, p):
            shots.append(p)
    monkeypatch.setattr(cc, "_PYAUTOGUI", True, raising=False)
    monkeypatch.setattr(cc, "pyautogui",
                        type("P", (), {"screenshot": staticmethod(lambda: Img())}), raising=False)
    monkeypatch.setattr(cc, "_safe_screenshot_path", lambda r: env["outside"] / "s.png")
    assert cc._screenshot("whatever").startswith(MARK)
    assert shots == []
    monkeypatch.setattr(cc, "_safe_screenshot_path", lambda r: env["rw"] / "s.png")
    assert cc._screenshot("whatever").startswith("Screenshot saved")
    assert len(shots) == 1


def test_browser_screenshot_outside_is_refused_and_default_is_checked(env, monkeypatch):
    bc = pytest.importorskip("actions.browser_control")
    import asyncio

    class Page:
        async def screenshot(self, path, full_page=False):
            open(path, "wb").write(b"x")

    sess = object.__new__(bc._BrowserSession)

    async def _get_page():
        return Page()
    sess._get_page = _get_page
    bad = env["outside"] / "s.png"
    assert asyncio.run(sess.screenshot(str(bad))).startswith(MARK)
    assert not bad.exists()
    # no path: the default (Desktop under the fake home) is not allowed either
    monkeypatch.setattr(bc.Path, "home", staticmethod(lambda: env["home"]))
    assert asyncio.run(sess.screenshot()).startswith(MARK)
    ok = env["rw"] / "s.png"
    assert asyncio.run(sess.screenshot(str(ok))).startswith("Screenshot saved")
    assert ok.exists()


# ── review fixes: file: URLs ─────────────────────────────────────────────────

def test_video_file_url_outside_is_refused_and_inside_plays(env):
    from actions import video_player as vp
    out_f = env["outside"] / "v.mp4"
    out_f.write_bytes(b"x")
    in_f = env["rw"] / "v.mp4"
    in_f.write_bytes(b"x")
    pl = FakePlayer()
    assert vp.video_player({"source": out_f.as_uri()}, player=pl).startswith(MARK)
    assert pl.shown == []
    assert vp.video_player({"source": in_f.as_uri()}, player=pl).startswith("Playing")
    assert len(pl.shown) == 1


def test_video_missing_outside_file_does_not_leak_existence(env):
    from actions import video_player as vp
    pl = FakePlayer()
    real = env["outside"] / "real.mp4"
    real.write_bytes(b"x")
    a = vp.video_player({"source": str(real)}, player=pl)
    b = vp.video_player({"source": str(env["outside"] / "nope.mp4")}, player=pl)
    assert a.startswith(MARK) and b.startswith(MARK)


def test_browser_file_url_is_checked_before_navigating(env, monkeypatch):
    bc = pytest.importorskip("actions.browser_control")
    seen = []
    monkeypatch.setattr(bc, "_open_native", lambda url, b: seen.append(url) or f"Opened {url}")
    monkeypatch.setattr(bc._registry, "has", lambda b=None: False)
    secret = env["outside"] / "s.txt"
    secret.write_text("s")
    ok = env["rw"] / "ok.html"
    ok.write_text("<p>")
    for act in ("go_to", "new_tab"):
        assert bc.browser_control({"action": act, "url": secret.as_uri()}).startswith(MARK)
    assert seen == []
    assert bc.browser_control({"action": "go_to", "url": ok.as_uri()}).startswith("Opened")
    assert bc.browser_control({"action": "go_to", "url": "https://example.com"}).startswith("Opened")


def test_browser_session_go_to_file_url_outside_is_refused(env):
    bc = pytest.importorskip("actions.browser_control")
    import asyncio
    sess = object.__new__(bc._BrowserSession)

    async def _get_page():
        raise AssertionError("must refuse before touching the page")
    sess._get_page = _get_page
    secret = env["outside"] / "s.txt"
    secret.write_text("s")
    assert asyncio.run(sess.go_to(secret.as_uri())).startswith(MARK)
