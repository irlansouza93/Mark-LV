"""Shared pytest setup for Mark LV.

Tests never import main.py / ui.py / google.genai and never touch the network;
every log file goes to a pytest tmp_path (constitution, principle II).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def log_dir(tmp_path):
    """Per-test logs folder (created by errlog.setup, not here)."""
    return tmp_path / "logs"


@pytest.fixture(autouse=True)
def _errlog_clean_after_test():
    """The writer thread, the tee and the excepthooks are process-wide: leave none behind."""
    yield
    mod = sys.modules.get("core.errlog")
    if mod is not None:
        mod.shutdown()
