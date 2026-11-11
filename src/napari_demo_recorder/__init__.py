"""Scripted, re-renderable demo GIFs of napari and other Qt apps."""

from __future__ import annotations

try:
    from ._version import version as __version__
except ImportError:  # pragma: no cover
    __version__ = "unknown"

from .core.builder import Demo
from .core.format import DemoError, DemoSpec, load_demo

__all__ = ["Demo", "DemoError", "DemoSpec", "load_demo", "render", "__version__"]


def render(path, output=None, **kwargs):
    """Render a ``*.demo.yaml`` file (needs a display, e.g. ``xvfb-run``)."""
    from .core.player import Player

    return Player(load_demo(path), **kwargs).run(output)
