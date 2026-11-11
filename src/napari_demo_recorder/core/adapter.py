"""App adapters.

The core knows only Qt.  An adapter teaches it about one application: how to start it, extra
step types, a UI catalogue, how to map canvas coordinates, and frame clean-up.  The generic
:class:`QtAdapter` runs any Qt application that exposes a function returning its main window::

    app: {factory: "my_package.gui:make_window"}

Additional adapters can be registered through the ``napari_demo_recorder.adapters`` entry point
group (value: ``module:AdapterClass``).
"""

from __future__ import annotations

import importlib
import sys
from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qtpy.QtCore import QPoint, QPointF, QThreadPool
from qtpy.QtWidgets import QApplication, QWidget

from .catalogue import Catalogue
from .format import DemoError
from .steps import CORE_STEPS, StepRegistry

if TYPE_CHECKING:
    from .player import Player
    from .selectors import Scope

BUILTIN_ADAPTERS = {
    "qt": "napari_demo_recorder.core.adapter:QtAdapter",
    "napari": "napari_demo_recorder.napari_app.adapter:NapariAdapter",
}


def import_object(path: str, base_dir: Path | None = None) -> Any:
    """Import ``"package.module:attr.sub"`` (``base_dir`` is put on ``sys.path`` first)."""
    if ":" not in path:
        raise DemoError(f"expected 'module:attribute', got {path!r}")
    mod_name, attr = path.split(":", 1)
    if base_dir is not None and str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    try:
        obj = importlib.import_module(mod_name)
    except ImportError as err:
        raise DemoError(f"cannot import {mod_name!r}: {err}") from None
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise DemoError(f"{mod_name!r} has no attribute {attr!r}") from None
    return obj


@cache
def adapter_class(name: str) -> type[Adapter]:
    path = BUILTIN_ADAPTERS.get(name)
    if path is None:
        for ep in entry_points(group="napari_demo_recorder.adapters"):
            if ep.name == name:
                return ep.load()
        if ":" in name:
            path = name
        else:
            raise DemoError(f"unknown app {name!r}. Known: {', '.join(BUILTIN_ADAPTERS)}")
    return import_object(path)


def registry_for(app_name: str) -> StepRegistry:
    from . import builtin_steps  # noqa: F401  (registers the core steps)

    return CORE_STEPS.merged(adapter_class(app_name).steps)


class Adapter:
    """Base class / generic behaviour for an app adapter."""

    name = "base"
    #: extra step types of this app
    steps: StepRegistry = StepRegistry()
    #: default value for ``coords`` when a step uses ``canvas:``
    default_coords = "fraction"

    def __init__(self, app_options: dict | None = None, base_dir: Path | None = None):
        self.options = dict(app_options or {})
        self.base_dir = base_dir
        self.window: QWidget | None = None
        self._catalogue: Catalogue | None = None

    # -- lifecycle -------------------------------------------------------------------------
    def start(self, window_options: dict) -> QWidget:
        """Create the application window (called by the player)."""
        raise NotImplementedError

    def attach(self, window: QWidget) -> None:
        """Use an already running window (recording inside a live app)."""
        self.window = window

    def close(self) -> None:
        pool = QThreadPool.globalInstance()
        if pool is not None:
            pool.waitForDone(5000)
        if self.window is not None:
            self.window.close()

    # -- environment for ``python:`` steps ---------------------------------------------------
    def namespace(self) -> dict[str, Any]:
        return {"window": self.window, "app": QApplication.instance()}

    # -- widgets ---------------------------------------------------------------------------
    @property
    def catalogue(self) -> Catalogue | None:
        return self._catalogue

    def resolve_parent(self, name: str, window: QWidget) -> Scope | None:
        from .selectors import scope_from_parent_def

        if self.catalogue is None:
            return None
        pdef = self.catalogue.parent(name)
        return scope_from_parent_def(pdef, window) if pdef is not None else None

    def canvas_widget(self) -> QWidget | None:
        return None

    # -- coordinates -----------------------------------------------------------------------
    def canvas_points(self, player: Player, points: list[list[float]], params: dict) -> list[QPoint]:
        """Map ``canvas:``/``path:`` points of a step to window coordinates."""
        coords = params.get("coords", self.default_coords if "canvas" in params else "fraction")
        widget = None
        if params.get("button") or params.get("widget") or params.get("class") or params.get("object_name"):
            widget = player.find(params)
        elif "canvas" in params:
            widget = self.canvas_widget()
            if widget is None:
                raise DemoError("this app has no canvas; give a 'widget' and a 'path' instead")
        else:
            widget = self.window
        return [self.widget_point(player, widget, p, coords) for p in points]

    def widget_point(self, player: Player, widget: QWidget, p, coords: str) -> QPoint:
        from .selectors import window_rect

        rect = window_rect(player.window, widget)
        if coords == "fraction":
            return QPoint(round(rect.left() + p[0] * rect.width()), round(rect.top() + p[1] * rect.height()))
        if coords in ("pixels", "canvas", "widget"):
            return QPoint(round(rect.left() + p[0]), round(rect.top() + p[1]))
        raise DemoError(f"coords {coords!r} not supported here (use 'fraction' or 'pixels')")

    def describe_point(self, widget: QWidget, local: QPoint | QPointF) -> dict | None:
        """Recorder: express a press inside ``widget`` as step params (canvas coords)."""
        return None

    def point_mapper(self, widget: QWidget):
        """Recorder: a function local-pos -> step params, frozen at press time (or None)."""
        if self.describe_point(widget, QPoint(0, 0)) is None:
            return None
        return lambda local: self.describe_point(widget, local)

    # -- frames ----------------------------------------------------------------------------
    def prepare_frame(self) -> None:
        """Hook to hide e.g. notification toasts before a frame is grabbed."""

    def is_idle(self) -> bool:
        return QThreadPool.globalInstance().activeThreadCount() == 0


class QtAdapter(Adapter):
    """Any Qt application: ``app: {factory: "module:function", ...kwargs}``."""

    name = "qt"
    steps = StepRegistry()

    def start(self, window_options: dict) -> QWidget:
        # keep a reference: a QApplication that is garbage-collected takes all widgets with it
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.app.setQuitOnLastWindowClosed(False)
        factory_path = self.options.get("factory")
        if not factory_path:
            raise DemoError("app: give a factory, e.g. app: {factory: 'my_package.gui:make_window'}")
        factory = import_object(factory_path, self.base_dir)
        kwargs = {k: v for k, v in self.options.items() if k not in ("factory", "name")}
        result = factory(**kwargs)
        window = getattr(result, "native", result)  # magicgui widgets
        if not isinstance(window, QWidget):
            raise DemoError(f"{factory_path} must return a QWidget (got {type(result).__name__})")
        self.window = window
        self._factory_result = result
        return window

    def namespace(self) -> dict[str, Any]:
        ns = super().namespace()
        ns["root"] = getattr(self, "_factory_result", self.window)
        return ns
