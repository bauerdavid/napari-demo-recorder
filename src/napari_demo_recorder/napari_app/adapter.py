"""napari adapter: viewer start-up, UI catalogue, data coordinates and napari-specific steps.

This module must not import napari at import time (schema / validation work without it).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from qtpy.QtCore import QPoint, QPointF, QThreadPool
from qtpy.QtWidgets import QAbstractItemView, QAbstractSlider, QWidget

from ..core.adapter import Adapter
from ..core.catalogue import Catalogue, norm
from ..core.format import DemoError
from ..core.selectors import window_rect
from ..core.steps import StepRegistry
from . import _compat

if TYPE_CHECKING:
    from ..core.player import Player

CATALOGUE_FILE = Path(__file__).with_name("napari_ui.yaml")

N = StepRegistry()


class NapariAdapter(Adapter):
    name = "napari"
    steps = N
    default_coords = "data"

    def __init__(self, app_options: dict | None = None, base_dir: Path | None = None):
        super().__init__(app_options, base_dir)
        self.viewer = None
        self._catalogue = Catalogue.from_file(CATALOGUE_FILE)

    # ------------------------------------------------------------------ lifecycle
    def start(self, window_options: dict):
        import napari

        title = self.options.get("title", "napari")
        self.viewer = napari.Viewer(show=True, title=title)
        theme = window_options.get("theme")
        if theme:
            self.viewer.theme = theme
        self.window = _compat.qt_window(self.viewer)
        return self.window

    def attach(self, window: QWidget, viewer=None) -> None:
        self.window = window
        self.viewer = viewer

    def close(self) -> None:
        QThreadPool.globalInstance().waitForDone(5000)
        if self.viewer is not None:
            with contextlib.suppress(Exception):
                _compat.close_viewer(self.viewer)

    def namespace(self) -> dict[str, Any]:
        import napari

        ns = super().namespace()
        ns.update(viewer=self.viewer, napari=napari)
        return ns

    # ------------------------------------------------------------------ widgets
    def canvas_widget(self) -> QWidget | None:
        return _compat.canvas_widget(self.window) if self.window is not None else None

    def default_key_target(self) -> QWidget | None:
        return self.canvas_widget()

    def prepare_frame(self) -> None:
        if self.window is not None:
            _compat.hide_notifications(self.window)

    # ------------------------------------------------------------------ coordinates
    def _layer(self, params: dict):
        layers = self.viewer.layers
        name = params.get("layer")
        if name is not None:
            if name not in layers:
                raise DemoError(f"no layer named {name!r} (layers: {[lyr.name for lyr in layers]})")
            return layers[name]
        active = layers.selection.active
        if active is not None:
            return active
        if len(layers):
            return layers[-1]
        return None

    def _displayed(self) -> list[int]:
        return list(self.viewer.dims.displayed)

    def world_to_canvas(self, world: np.ndarray) -> QPointF:
        canvas = self.canvas_widget()
        cam = self.viewer.camera
        disp = self._displayed()
        if len(disp) != 2:
            raise DemoError("canvas coordinates are only supported in 2D display")
        center = np.asarray(cam.center, dtype=float)[-2:]
        w = np.asarray(world, dtype=float)[disp]
        y = (w[0] - center[0]) * cam.zoom + canvas.height() / 2
        x = (w[1] - center[1]) * cam.zoom + canvas.width() / 2
        return QPointF(x, y)

    def canvas_to_world(self, pos: QPointF) -> np.ndarray:
        canvas = self.canvas_widget()
        cam = self.viewer.camera
        disp = self._displayed()
        world = np.asarray(self.viewer.dims.point, dtype=float).copy()
        center = np.asarray(cam.center, dtype=float)[-2:]
        world[disp[0]] = center[0] + (pos.y() - canvas.height() / 2) / cam.zoom
        world[disp[1]] = center[1] + (pos.x() - canvas.width() / 2) / cam.zoom
        return world

    def data_to_world(self, layer, coords) -> np.ndarray:
        coords = list(coords)
        ndim_world = self.viewer.dims.ndim
        if len(coords) == layer.ndim:
            data = np.asarray(coords, dtype=float)
        elif len(coords) == 2:
            # 2 values: the displayed axes; the others come from the current slice
            data = np.asarray(layer.world_to_data(self.viewer.dims.point), dtype=float)
            data[-2:] = coords
        else:
            raise DemoError(f"point {coords} has {len(coords)} values; layer {layer.name!r} has {layer.ndim} dims")
        world = np.asarray(layer.data_to_world(data), dtype=float)
        if len(world) < ndim_world:  # layer with fewer dims than the viewer
            full = np.asarray(self.viewer.dims.point, dtype=float).copy()
            full[-len(world):] = world
            world = full
        return world

    def canvas_points(self, player: Player, points, params: dict) -> list[QPoint]:
        if "canvas" not in params:
            return super().canvas_points(player, points, params)
        coords = params.get("coords", self.default_coords)
        canvas = self.canvas_widget()
        if canvas is None:
            raise DemoError("napari canvas not found")
        rect = window_rect(player.window, canvas)
        out = []
        for p in points:
            if coords == "data":
                layer = self._layer(params)
                if layer is None:
                    raise DemoError("canvas data coordinates need a layer (add 'layer:' or use coords: world)")
                local = self.world_to_canvas(self.data_to_world(layer, p))
            elif coords == "world":
                world = np.asarray(self.viewer.dims.point, dtype=float).copy()
                disp = self._displayed()
                if len(p) == len(world):
                    world = np.asarray(p, dtype=float)
                else:
                    world[disp] = p[-2:]
                local = self.world_to_canvas(world)
            elif coords == "fraction":
                local = QPointF(p[0] * canvas.width(), p[1] * canvas.height())
            elif coords in ("canvas", "pixels"):
                local = QPointF(p[0], p[1])
            else:
                raise DemoError(f"unknown coords {coords!r} (data, world, fraction, pixels)")
            out.append(rect.topLeft() + local.toPoint())
        return out

    def describe_point(self, widget: QWidget, local) -> dict | None:
        """Recorder: canvas pixel -> data coordinates of the selected layer."""
        mapper = self.point_mapper(widget)
        return mapper(local) if mapper is not None else None

    def point_mapper(self, widget: QWidget):
        """Freeze camera + layer now: a drag that pans or zooms must be mapped with the
        transform from the moment the button was pressed."""
        if widget is not self.canvas_widget() or self.viewer is None or len(self._displayed()) != 2:
            return None
        cam = self.viewer.camera
        center = np.asarray(cam.center, dtype=float)[-2:].copy()
        zoom = float(cam.zoom)
        disp = self._displayed()
        point = np.asarray(self.viewer.dims.point, dtype=float).copy()
        size = (widget.width(), widget.height())
        layer = self._layer({})

        def mapper(local) -> dict:
            local = QPointF(local)
            world = point.copy()
            world[disp[0]] = center[0] + (local.y() - size[1] / 2) / zoom
            world[disp[1]] = center[1] + (local.x() - size[0] / 2) / zoom
            if layer is None:
                return {"canvas": [round(float(world[d]), 1) for d in disp], "coords": "world"}
            data = np.asarray(layer.world_to_data(world), dtype=float)
            return {"canvas": [round(float(v), 1) for v in data[-2:]], "layer": layer.name}

        return mapper

    # ------------------------------------------------------------------ recorder hooks
    def record_press(self, recorder, press, moved) -> tuple[str, dict] | None:
        """Layer-list rows and dims sliders become semantic steps."""
        w = press.widget
        view = w if isinstance(w, QAbstractItemView) else w.parentWidget()
        if isinstance(view, QAbstractItemView) and type(view).__name__ == "QtLayerList" and moved < 4:
            local = press.local if w is view.viewport() else view.viewport().mapFrom(w, press.local)
            idx = view.indexAt(local)
            if not idx.isValid():
                return None
            name = str(view.model().data(idx))
            check = _check_rect(view, idx)
            rect = view.visualRect(idx)
            on_eye = check.contains(local) if check is not None else local.x() - rect.left() < min(rect.height(), 28)
            if on_eye:
                return "toggle_visibility", {"layer": name}
            params = {"layer": name}
            if press.modifiers & 0x04000000:
                params["add"] = True
            return "select_layer", params
        if isinstance(w, QAbstractSlider) and type(w.parentWidget()).__name__ == "QtDimSliderWidget":
            axis = getattr(w.parentWidget(), "axis", None)
            if axis is not None:
                return "dims", {"axis": int(axis), "value": int(w.value())}
        return None


# =============================================================================== steps


def _adapter(p: Player) -> NapariAdapter:
    return p.adapter  # type: ignore[return-value]


@N.step(
    "open_sample",
    params={"plugin": {"type": "string"}, "sample": {"type": "string"}},
    required=["plugin", "sample"],
    shorthand=["plugin", "sample"],
)
def open_sample(p: Player, params: dict):
    """Open a sample (``viewer.open_sample(plugin, sample)``), e.g. [napari, cells3d]."""
    _adapter(p).viewer.open_sample(params["plugin"], params["sample"])
    p.settle(0.2)
    yield


@N.step(
    "open_file",
    params={"path": {"type": "string"}, "plugin": {"type": "string"}, "layer_type": {"type": "string"}},
    required=["path"],
    shorthand="path",
)
def open_file(p: Player, params: dict):
    """Open a file (relative to the demo file) with ``viewer.open``."""
    path = Path(params["path"])
    if not path.is_absolute():
        path = p.spec.base_dir / path
    kwargs = {k: params[k] for k in ("plugin", "layer_type") if k in params}
    _adapter(p).viewer.open(str(path), **kwargs)
    p.settle(0.2)
    yield


@N.step(
    "open_plugin",
    params={
        "plugin": {"type": "string"},
        "widget": {"type": "string"},
        "area": {"enum": ["right", "left", "bottom", "top"]},
        "tabify": {"type": "boolean"},
    },
    required=["plugin"],
    shorthand=["plugin", "widget"],
)
def open_plugin(p: Player, params: dict):
    """Open a plugin's dock widget, e.g. [napari-nD-annotator, Annotation Toolbox]."""
    viewer = _adapter(p).viewer
    kwargs = {}
    if "tabify" in params:
        kwargs["tabify"] = params["tabify"]
    try:
        viewer.window.add_plugin_dock_widget(params["plugin"], params.get("widget"), **kwargs)
    except KeyError as err:
        raise DemoError(f"open_plugin: {err}") from None
    p.settle(0.3)
    yield


def _layer_list(p: Player) -> QAbstractItemView:
    from ..core.selectors import scope_from_parent_def

    pdef = _adapter(p).catalogue.parents["layer_list"]
    roots = scope_from_parent_def(pdef, p.window).roots
    views = [r for r in roots if isinstance(r, QAbstractItemView)]
    if not views:
        views = [w for r in roots for w in r.findChildren(QAbstractItemView) if w.isVisible()]
    if not views:
        raise DemoError("layer list not found (is it hidden?)")
    return views[0]


def _check_rect(view: QAbstractItemView, idx):
    """The visibility check box of a row, as napari's delegate computes it (viewport coords)."""
    from qtpy.QtWidgets import QStyle, QStyleOptionViewItem

    opt = QStyleOptionViewItem()
    opt.rect = view.visualRect(idx)
    try:
        view.itemDelegate().initStyleOption(opt, idx)
        opt.widget = view
    except Exception:
        return None
    r = view.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, view)
    return r if r.isValid() and r.width() > 0 else None


def _layer_row_rect(p: Player, name: str):
    view = _layer_list(p)
    names = _compat.layer_names_in_list(view)
    if name not in names:
        hits = [n for n in names if norm(n) == norm(name)]
        if not hits:
            raise DemoError(f"no layer {name!r} in the layer list ({names})")
        name = hits[0]
    m = view.model()
    idx = m.index(names.index(name), 0)
    view.scrollTo(idx)
    r = view.visualRect(idx)
    vp = view.viewport()
    origin = vp.mapToGlobal(QPoint(0, 0)) - p.window.mapToGlobal(QPoint(0, 0))
    check = _check_rect(view, idx)
    eye = origin + check.center() if check is not None else None
    return origin + r.topLeft(), r.size(), eye


@N.step(
    "select_layer",
    params={"layer": {"type": "string"}, "add": {"type": "boolean", "description": "Ctrl-click to add to the selection."}},
    required=["layer"],
    shorthand="layer",
)
def select_layer(p: Player, params: dict):
    """Click a layer in the layer list."""
    top_left, size, _eye = _layer_row_rect(p, params["layer"])
    pt = top_left + QPoint(int(size.width() * 0.55), size.height() // 2)
    if params.get("add"):
        from ..core.builtin_steps import _modifiers

        with _modifiers(p, "Ctrl"):
            p.show_keys("Ctrl + click")
            yield from p.click(pt)
    else:
        yield from p.click(pt)


@N.step(
    "toggle_visibility",
    params={"layer": {"type": "string"}},
    required=["layer"],
    shorthand="layer",
)
def toggle_visibility(p: Player, params: dict):
    """Click the eye icon of a layer in the layer list."""
    viewer = _adapter(p).viewer
    layer = viewer.layers[params["layer"]] if params["layer"] in viewer.layers else None  # noqa: SIM401
    before = layer.visible if layer is not None else None
    top_left, size, eye = _layer_row_rect(p, params["layer"])
    pt = eye if eye is not None else top_left + QPoint(min(size.height() // 2 + 2, 20), size.height() // 2)
    yield from p.click(pt)
    if layer is not None and layer.visible == before:  # delegate geometry differs: do it directly
        layer.visible = not before
        yield


@N.step(
    "dims",
    params={"axis": {"type": "integer"}, "value": {"type": "number"}},
    required=["axis", "value"],
    shorthand=["axis", "value"],
)
def dims(p: Player, params: dict):
    """Drag the dims slider of ``axis`` to ``value`` (e.g. a z-slice)."""
    from ..core.builtin_steps import _set_slider

    slider = None
    for w in p.window.findChildren(QAbstractSlider):
        parent = w.parentWidget()
        if w.isVisible() and type(parent).__name__ == "QtDimSliderWidget" and getattr(parent, "axis", None) == params["axis"]:
            slider = w
            break
    if slider is None:
        raise DemoError(f"dims: no visible slider for axis {params['axis']}")
    yield from _set_slider(p, slider, params["value"], None)
    _adapter(p).viewer.dims.set_current_step(params["axis"], params["value"])


@N.step(
    "camera",
    params={
        "zoom": {"type": "number"},
        "center": {"type": "array", "items": {"type": "number"}},
        "angles": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
        "ndisplay": {"enum": [2, 3]},
        "reset": {"type": "boolean", "description": "Reset the view first (like the Home button)."},
    },
)
def camera(p: Player, params: dict):
    """Animate the camera (zoom / center / angles) over ``duration`` (default 1 s)."""
    from ..core.player import ease

    viewer = _adapter(p).viewer
    if params.get("ndisplay"):
        viewer.dims.ndisplay = params["ndisplay"]
    if params.get("reset"):
        viewer.reset_view()
    cam = viewer.camera
    start = {"zoom": float(cam.zoom), "center": np.asarray(cam.center, float), "angles": np.asarray(cam.angles, float)}
    end = dict(start)
    if "zoom" in params:
        end["zoom"] = float(params["zoom"])
    if "center" in params:
        c = np.asarray(start["center"]).copy()
        given = np.asarray(params["center"], float)
        c[-len(given):] = given
        end["center"] = c
    if "angles" in params:
        end["angles"] = np.asarray(params["angles"], float)
    n = p.n_frames(p.duration(params, 1.0))
    for i in range(1, n + 1):
        u = ease(i / n)
        cam.zoom = start["zoom"] * (end["zoom"] / start["zoom"]) ** u if start["zoom"] > 0 else end["zoom"]
        cam.center = tuple(start["center"] + (end["center"] - start["center"]) * u)
        if "angles" in params:
            cam.angles = tuple(start["angles"] + (end["angles"] - start["angles"]) * u)
        yield
