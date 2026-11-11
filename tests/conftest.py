import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(EXAMPLES))

# never touch the developer's real napari settings
os.environ.setdefault("NAPARI_CONFIG", str(Path(tempfile.mkdtemp()) / "settings.yaml"))


@pytest.fixture
def examples() -> Path:
    return EXAMPLES


def pytest_collection_modifyitems(config, items):
    try:
        import napari  # noqa: F401
    except ImportError:
        skip = pytest.mark.skip(reason="napari not installed")
        for item in items:
            if "napari" in item.keywords:
                item.add_marker(skip)


def pytest_configure(config):
    config.addinivalue_line("markers", "napari: needs napari")


_EXIT = {"status": 0}


def pytest_sessionfinish(session, exitstatus):
    _EXIT["status"] = int(exitstatus)
    try:
        from qtpy.QtCore import QThreadPool
        from qtpy.QtWidgets import QApplication

        QThreadPool.globalInstance().waitForDone(5000)
        if QApplication.instance() is not None:
            QApplication.processEvents()
    except Exception:
        pass


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config):
    # napari / vispy objects torn down at interpreter exit can abort the process
    # ("QThread: Destroyed while thread is still running") after all tests passed.
    # Leave immediately with pytest's real exit status instead.
    import sys

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT["status"])
