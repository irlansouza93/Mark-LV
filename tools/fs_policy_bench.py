"""Cost of the folder policy on a big search (spec 003, SC-005: at most 10 % slower).

Builds 10 000 files (100 folders of 100) in a temp dir, then times file_controller `find`
five times through fs_policy.walk and five times with fs_policy.walk swapped for a bare
os.walk, and prints the medians. The search matches nothing, so every file is visited.

    .venv/Scripts/python.exe tools/fs_policy_bench.py
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from actions import file_controller as fc  # noqa: E402
from core import fs_policy  # noqa: E402

RUNS = 5


def _bare_walk(root, *, actor):
    for dp, dirs, files in os.walk(root, followlinks=False):
        yield Path(dp), list(dirs), files


def _time(root: str) -> float:
    t = time.perf_counter()
    out = fc.find_files(name="zz-no-such-file", path=root, max_results=20)
    dt = time.perf_counter() - t
    assert out.startswith("No "), out
    return dt


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "tree"
        for d in range(100):
            sub = base / f"d{d:03d}"
            sub.mkdir(parents=True)
            for f in range(100):
                (sub / f"file{f:03d}.txt").write_bytes(b"x")
        fs_policy.configure(folders=[(base, "ro")], home=Path(tmp) / "home",
                            app_dir=Path(tmp) / "app", env={}, uploaded=None)
        real_walk = fs_policy.walk
        root = str(base)
        _time(root)                                   # warm the file cache for both
        with_policy = [_time(root) for _ in range(RUNS)]
        fs_policy.walk = _bare_walk
        try:
            _time(root)
            bare = [_time(root) for _ in range(RUNS)]
        finally:
            fs_policy.walk = real_walk
            fs_policy.reset()
    a, b = statistics.median(with_policy), statistics.median(bare)
    print(f"10000 files, find x{RUNS}")
    print(f"  with fs_policy.walk : median {a * 1000:7.1f} ms   runs {[round(x * 1000) for x in with_policy]}")
    print(f"  bare os.walk        : median {b * 1000:7.1f} ms   runs {[round(x * 1000) for x in bare]}")
    print(f"  difference          : {(a - b) / b * 100:+.1f} %  (target <= 10 %)")


if __name__ == "__main__":
    main()
