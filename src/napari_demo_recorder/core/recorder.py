"""Record what a person does in a running app and turn it into demo steps.

An application-wide event filter watches real (spontaneous) mouse and keyboard events and turns
them into *semantic* steps - ``click: {button: Detect, parent: Blob finder}``, ``set: {widget:
sigma, value: 4.5}``, ``drag: {canvas: [...]}`` in data coordinates - rather than pixel
positions, so the result keeps working when the layout changes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qtpy.QtCore import QEvent, QObject, QPoint, QTimer
from qtpy.QtGui import QKeySequence
from qtpy.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDockWidget,
    QLabel,
    QLineEdit,
    QMenu,
    QMenuBar,
    QWidget,
)

from .catalogue import norm, tooltip_keys, widget_matches_find
from .format import Step
from .qt_compat import as_int, event_global_pos, event_pos
from .selectors import (
    INTERNAL_PREFIX,
    find_widgets,
    label_target,
    magic_name,
    popups,
    scope_from_parent_def,
    widget_text,
    window_rect,
)

CLICK_SLOP = 4  # px: a press/release closer than this is a click, not a drag
MODIFIER_KEYS = {"Shift", "Ctrl", "Alt", "Meta", "Control", "AltGr", "CapsLock"}


@dataclass
class _Press:
    widget: QWidget
    window_pos: QPoint
    local: QPoint
    button: str
    t: float
    modifiers: int
    path: list[QPoint] = field(default_factory=list)
    #: canvas point mapper frozen at press time (camera may move during the drag)
    mapper: Callable | None = None


def _button_name(ev) -> str:
    b = as_int(ev.button())
    return {1: "left", 2: "right", 4: "middle"}.get(b, "left")


def _rdp(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker polyline simplification."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm_ = (dx * dx + dy * dy) ** 0.5 or 1e-9
    dmax, idx = 0.0, 0
    for i, (x, y) in enumerate(points[1:-1], start=1):
        d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm_
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _rdp(points[: idx + 1], eps)[:-1] + _rdp(points[idx:], eps)
    return [points[0], points[-1]]


class Recorder(QObject):
    """Install with :meth:`start`, get the steps with :meth:`stop`."""

    def __init__(
        self,
        window: QWidget,
        adapter=None,
        *,
        ignore: list[QWidget] | None = None,
        holds: bool = True,
        spontaneous_only: bool = True,
    ):
        super().__init__()
        #: False records synthetic events too (tests drive the app with the player)
        self.spontaneous_only = spontaneous_only
        self.window = window
        self.adapter = adapter
        self.ignore = list(ignore or [])
        self.holds = holds
        self.steps: list[Step] = []
        self.on_step: Callable[[Step], None] | None = None
        self._press: _Press | None = None
        self._editing: QWidget | None = None
        self._edit_target: dict | None = None
        self._last_t = 0.0
        self._last_event = None
        self._wheel: dict | None = None
        self.active = False

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        self.steps = []
        self._last_t = time.perf_counter()
        QApplication.instance().installEventFilter(self)
        self.active = True

    def stop(self) -> list[Step]:
        if self.active:
            QApplication.instance().removeEventFilter(self)
            self.active = False
        self._flush_edit()
        self._flush_wheel()
        return list(self.steps)

    # ------------------------------------------------------------------ helpers
    def _add(self, name: str, params: dict) -> None:
        now = time.perf_counter()
        gap = now - self._last_t
        if self.holds and self.steps and gap > 1.5:
            self.steps.append(Step("hold", {"seconds": round(min(3.0, gap * 0.5) * 2) / 2}))
        self._last_t = now
        step = Step(name, params)
        self.steps.append(step)
        if self.on_step is not None:
            self.on_step(step)

    def _relevant(self, w: QWidget) -> bool:
        if w.objectName().startswith(INTERNAL_PREFIX):
            return False
        p = w
        while p is not None:
            if p.objectName().startswith(INTERNAL_PREFIX) or any(p is ig for ig in self.ignore):
                return False
            p = p.parentWidget()
        top = w.window()
        return top is self.window or top in popups() or isinstance(top, QMenu)

    def _window_pos(self, ev) -> QPoint:
        return event_global_pos(ev) - self.window.mapToGlobal(QPoint(0, 0))

    # ------------------------------------------------------------------ event filter
    def eventFilter(self, obj, ev):  # noqa: N802
        try:
            self._handle(obj, ev)
        except Exception as err:  # never break the app while recording
            print(f"[demo-recorder] recorder error: {err}")
        return False

    def _handle(self, obj, ev) -> None:
        et = ev.type()
        if not isinstance(obj, QWidget):
            return
        if et in (QEvent.Type.FocusOut,) and obj is self._editing:
            QTimer.singleShot(0, self._flush_edit)
            return
        if self.spontaneous_only and not ev.spontaneous():
            return
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseMove,
                  QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel, QEvent.Type.KeyPress):  # fmt: skip
            # the same event object is re-delivered to parents when ignored - handle it once
            key = (id(ev), et)
            if key == self._last_event:
                return
            self._last_event = key
        if not self._relevant(obj):
            return
        if et == QEvent.Type.MouseButtonPress:
            self._flush_wheel()
            wp = self._window_pos(ev)
            mapper = self.adapter.point_mapper(obj) if self.adapter is not None else None
            self._press = _Press(obj, wp, event_pos(ev), _button_name(ev), time.perf_counter(),
                                 as_int(ev.modifiers()), [wp], mapper)  # fmt: skip
        elif et == QEvent.Type.MouseMove and self._press is not None:
            wp = self._window_pos(ev)
            last = self._press.path[-1]
            if abs(wp.x() - last.x()) + abs(wp.y() - last.y()) >= 3:
                self._press.path.append(wp)
        elif et == QEvent.Type.MouseButtonRelease and self._press is not None:
            press, self._press = self._press, None
            press.path.append(self._window_pos(ev))
            self._on_release(press, obj, ev)
        elif et == QEvent.Type.MouseButtonDblClick:
            if self.steps and self.steps[-1].name == "click":
                self.steps[-1].params["double"] = True
        elif et == QEvent.Type.Wheel:
            self._on_wheel(obj, ev)
        elif et == QEvent.Type.KeyPress:
            self._on_key(obj, ev)

    # ------------------------------------------------------------------ mouse
    def _on_release(self, press: _Press, obj: QWidget, ev) -> None:
        w = press.widget
        start = press.path[0]
        moved = max(abs(p.x() - start.x()) + abs(p.y() - start.y()) for p in press.path)
        mods = _modifier_text(press.modifiers)

        # menus: record the whole path once a leaf action is triggered
        if isinstance(w, QMenuBar):
            return
        if isinstance(obj, QMenu) or isinstance(w, QMenu):
            menu = obj if isinstance(obj, QMenu) else w
            action = menu.actionAt(menu.mapFromGlobal(event_global_pos(ev)))
            if action is not None and action.menu() is None and not action.isSeparator():
                self._add("menu", {"path": _menu_path(menu, action)})
            return
        # combo-box popup list: record the selected item on the combo box
        combo = _owning_combo(w)
        if combo is not None:
            if w is combo:
                return  # opening the popup
            QTimer.singleShot(0, lambda c=combo: self._add("set", {**self.selector(c), "value": c.currentText()}))
            return
        # adapter-specific (napari: layer list rows, dims sliders, canvas)
        if self.adapter is not None and hasattr(self.adapter, "record_press"):
            step = self.adapter.record_press(self, press, moved)
            if step is not None:
                self._add(*step)
                return
        # canvas: click or drag in data coordinates
        if press.mapper is not None:
            first = press.mapper(press.local)
            if first is not None:
                if moved < CLICK_SLOP:
                    params = dict(first)
                    params["canvas"] = first["canvas"]
                    if press.button != "left":
                        params["mouse"] = press.button
                    if mods:
                        params["keys"] = mods
                    self._add("click", params)
                else:
                    self._add("drag", self._canvas_drag(w, press, mods))
                return
        # sliders: record the final value
        slider = _owning(w, QAbstractSlider)
        if slider is not None:
            outer = slider
            while isinstance(outer.parentWidget(), QAbstractSlider):
                outer = outer.parentWidget()
            QTimer.singleShot(0, lambda s=outer: self._add("set", {**self.selector(s), "value": _slider_value(s)}))
            return
        # text fields / spin boxes: record a 'set' when editing finishes
        editable = _owning(w, (QLineEdit, QAbstractSpinBox))
        if editable is not None:
            spin = _owning(editable, QAbstractSpinBox)
            target = spin or editable
            if self._editing is not target:
                self._flush_edit()
                self._editing = target
                self._edit_target = self.selector(target)
            if moved < CLICK_SLOP and spin is None:
                return
            if spin is not None:  # arrow clicks change the value immediately
                QTimer.singleShot(0, lambda s=spin: self._edit_changed(s))
            return
        # plain widgets
        if moved < CLICK_SLOP:
            target = _clickable(w)
            params = self.selector(target)
            if press.button != "left":
                params["mouse"] = press.button
            if mods:
                params["keys"] = mods
            self._add("click", params)
        else:
            params = {"at": [[p.x(), p.y()] for p in _simplify(press.path)]}
            self._add("drag", params)

    def _canvas_drag(self, widget: QWidget, press: _Press, mods: str) -> dict:
        pts = _simplify(press.path)
        rect = window_rect(self.window, widget)
        coords = []
        info: dict = {}
        for p in pts:
            d = press.mapper(p - rect.topLeft())
            if d is None:
                continue
            coords.append(d["canvas"])
            info = {k: v for k, v in d.items() if k != "canvas"}
        params = {"canvas": coords, **info}
        if press.button != "left":
            params["mouse"] = press.button
        if mods:
            params["keys"] = mods
        return params

    def _on_wheel(self, obj: QWidget, ev) -> None:
        delta = ev.angleDelta().y() / 120.0
        now = time.perf_counter()
        if self._wheel is not None and self._wheel["widget"] is obj and now - self._wheel["t"] < 0.6:
            self._wheel["steps"] += delta
            self._wheel["t"] = now
            return
        self._flush_wheel()
        local = event_pos(ev)
        target = self.adapter.describe_point(obj, local) if self.adapter is not None else None
        if target is None:
            target = self.selector(obj)
        self._wheel = {"widget": obj, "steps": delta, "t": now, "target": target}

    def _flush_wheel(self) -> None:
        if self._wheel is not None and self._wheel["steps"]:
            self._add("scroll", {**self._wheel["target"], "steps": round(self._wheel["steps"], 2)})
        self._wheel = None

    # ------------------------------------------------------------------ keyboard
    def _on_key(self, obj: QWidget, ev) -> None:
        if ev.isAutoRepeat():
            return
        key = as_int(ev.key())
        name = QKeySequence(key).toString()
        if not name or name in MODIFIER_KEYS:
            return
        editing = self._editing is not None and (obj is self._editing or self._editing.isAncestorOf(obj))
        if editing:
            if name in ("Return", "Enter"):
                QTimer.singleShot(0, self._flush_edit)
            return
        if isinstance(obj, QLineEdit | QAbstractSpinBox):
            # typing into a field focused with Tab etc.
            self._editing = _owning(obj, QAbstractSpinBox) or obj
            self._edit_target = self.selector(self._editing)
            return
        mods = _modifier_text(as_int(ev.modifiers()))
        keys = f"{mods}+{name}" if mods else name
        if self.steps and self.steps[-1].name == "key" and self.steps[-1].params["keys"] == keys and time.perf_counter() - self._last_t < 0.6:
            self.steps[-1].params["times"] = self.steps[-1].params.get("times", 1) + 1
            self._last_t = time.perf_counter()
            return
        self._add("key", {"keys": keys})

    def _edit_changed(self, spin: QAbstractSpinBox) -> None:
        self._editing = spin
        self._edit_target = self.selector(spin)
        self._flush_edit()

    def _flush_edit(self) -> None:
        w, target = self._editing, self._edit_target
        self._editing, self._edit_target = None, None
        if w is None or target is None:
            return
        try:
            value = w.value() if hasattr(w, "value") else w.text()
        except RuntimeError:
            return
        if (self.steps and self.steps[-1].name == "set"
                and {k: v for k, v in self.steps[-1].params.items() if k != "value"} == target):  # fmt: skip
            self.steps[-1].params["value"] = value
            return
        self._add("set", {**target, "value": value})

    # ------------------------------------------------------------------ selectors
    def selector(self, widget: QWidget) -> dict:
        """Robust target keys for ``widget`` (verified to resolve back to it)."""
        parent = self._parent_name(widget)
        tries: list[dict] = []
        cat = self.adapter.catalogue if self.adapter is not None else None
        if cat is not None:
            for el in cat.elements:
                if any(widget_matches_find(widget, c) for c in el.find):
                    name = el.aliases[0] if el.aliases else el.id
                    tries.append({"button": name, **({"parent": el.parent} if el.parent else {})})
        is_button = isinstance(widget, QAbstractButton)
        key = "button" if is_button else "widget"
        text = widget_text(widget)
        if text and text.strip() and not isinstance(widget, QLabel):
            tries.append({key: text.strip()})
        mname = magic_name(widget) or (magic_name(widget.parentWidget()) if widget.parentWidget() else None)
        if mname:
            tries.append({"widget": mname})
        label = _label_for(widget)
        if label:
            tries.append({"widget": label})
        tips = tooltip_keys(widget.toolTip())
        if tips:
            first = widget.toolTip().strip().split("\n")[0].strip()
            tries.append({key: first.rstrip(". ") if norm(first) == tips[0] else tips[0]})
        if widget.objectName() and not widget.objectName().startswith("qt_"):
            tries.append({key: widget.objectName()})
        for t in tries:
            for params in ([{**t, "parent": parent}] if parent and "parent" not in t else []) + [t]:
                found = find_widgets(self.window, params, self.adapter)
                if found and found[0] is widget and len(found) == 1:
                    return params
                if widget in found:
                    return {**params, "index": found.index(widget)}
        # last resort: a position (fragile, but replayable)
        c = window_rect(self.window, widget).center()
        return {"at": [c.x(), c.y()]}

    def _parent_name(self, widget: QWidget) -> str | None:
        cat = self.adapter.catalogue if self.adapter is not None else None
        if cat is not None:
            best = None
            for name, pdef in cat.parents.items():
                if pdef.window:
                    continue
                scope = scope_from_parent_def(pdef, self.window)
                if scope.roots and scope.contains(widget):
                    size = min(r.width() * r.height() for r in scope.roots)
                    if best is None or size < best[1]:
                        best = (name, size)
            if best is not None:
                return best[0]
        p = widget.parentWidget()
        while p is not None:
            if isinstance(p, QDockWidget) and p.windowTitle():
                return p.windowTitle()
            p = p.parentWidget()
        return None


# ---------------------------------------------------------------------- helpers


def _modifier_text(mods: int) -> str:
    names = []
    for flag, name in ((0x04000000, "Ctrl"), (0x08000000, "Alt"), (0x02000000, "Shift"), (0x10000000, "Meta")):
        if mods & flag:
            names.append(name)
    return "+".join(names)


def _owning(w: QWidget | None, cls):
    while w is not None:
        if isinstance(w, cls):
            return w
        if w.isWindow():
            return None
        w = w.parentWidget()
    return None


def _owning_combo(w: QWidget) -> QComboBox | None:
    if isinstance(w, QComboBox):
        return w
    view = _owning(w, QAbstractItemView)
    if view is not None:
        p = view.parentWidget()
        while p is not None:
            if isinstance(p, QComboBox):
                return p
            p = p.parentWidget()
        # the popup container is a separate window whose parent is the combo
        top = view.window()
        par = top.parentWidget()
        if isinstance(par, QComboBox):
            return par
    return None


def _clickable(w: QWidget) -> QWidget:
    btn = _owning(w, QAbstractButton)
    return btn if btn is not None else w


def _label_for(widget: QWidget) -> str | None:
    p = widget.parentWidget()
    for _ in range(3):
        if p is None:
            break
        for lab in p.findChildren(QLabel):
            if lab.text().strip() and label_target(lab) in (widget, widget.parentWidget()):
                return lab.text().strip().rstrip(":")
        p = p.parentWidget()
    return None


def _slider_value(s: QAbstractSlider):
    v = s.value()
    mw = getattr(s.parentWidget(), "_magic_widget", None) or getattr(s, "_magic_widget", None)
    if mw is not None and hasattr(mw, "value"):
        v = mw.value
    if isinstance(v, float):
        v = round(v, 4)
    return v


def _menu_path(menu: QMenu, action) -> list[str]:
    from .builtin_steps import _strip_mnemonic

    path = [_strip_mnemonic(action.text())]
    m = menu
    while m is not None:
        owner = m.menuAction()
        parent_menu = None
        for w in QApplication.allWidgets():
            if isinstance(w, QMenu | QMenuBar) and owner in w.actions() and w is not m:
                parent_menu = w
                break
        path.insert(0, _strip_mnemonic(owner.text() or m.title()))
        if parent_menu is None or isinstance(parent_menu, QMenuBar):
            break
        m = parent_menu
    return path


def _simplify(path: list[QPoint]) -> list[QPoint]:
    pts = [(p.x(), p.y()) for p in path]
    out = _rdp(pts, 2.0)
    return [QPoint(round(x), round(y)) for x, y in out]


# ---------------------------------------------------------------------- standalone recording


def steps_to_demo(steps: list[Step], *, app: str | dict, window: QWidget, setup: list[Step] | None = None,
                  output: str | None = None) -> str:  # fmt: skip
    from .builder import Demo

    demo = Demo(app=app, size=(window.width(), window.height()), output=output)
    if setup:
        demo.setup.steps = list(setup)
    demo.steps.steps = list(steps)
    return demo.to_yaml()


def record_app(app: str, output: str, setup: str | None = None) -> int:
    """``napari-demo-recorder record``: start the app, record until the control window is closed."""
    from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton

    from .format import DemoSpec, load_demo
    from .player import Player

    out_path = Path(output)
    if setup:
        spec = load_demo(setup)
        spec.steps = []
    else:
        spec = DemoSpec(data={"demo": 1, "app": app, "steps": []}, path=out_path.resolve())
    player = Player(spec, mode="run", log=print)
    player.run(close=False)  # starts the app and runs the setup
    window = player.window
    adapter = player.adapter
    app_obj = QApplication.instance()

    control = QWidget()
    control.setObjectName(INTERNAL_PREFIX + "control")
    control.setWindowTitle("Demo recorder")
    lay = QHBoxLayout(control)
    status = QLabel("● Recording - use the app, then press Stop")
    status.setStyleSheet("color: #d33; font-weight: bold")
    stop_btn = QPushButton("Stop && save")
    lay.addWidget(status)
    lay.addWidget(stop_btn)
    recorder = Recorder(window, adapter, ignore=[control])
    recorder.on_step = lambda s: status.setText(f"● {len(recorder.steps)} steps - last: {s.name}")

    def finish():
        steps = recorder.stop()
        app_spec = spec.data.get("app", app)
        text = steps_to_demo(steps, app=app_spec, window=window, setup=spec.setup)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"wrote {out_path} ({len(steps)} steps)")
        control.close()
        app_obj.quit()

    stop_btn.clicked.connect(finish)
    control.show()
    recorder.start()
    app_obj.exec() if hasattr(app_obj, "exec") else app_obj.exec_()
    return 0
