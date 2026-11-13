"""Steps available for every Qt app."""

from __future__ import annotations

import inspect
import re
import time
from typing import TYPE_CHECKING

from qtpy.QtCore import QPoint
from qtpy.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QSlider,
    QStyle,
    QTextEdit,
    QWidget,
)

from .catalogue import norm
from .format import DemoError
from .qt_compat import key_display
from .steps import CORE_STEPS as S

if TYPE_CHECKING:
    from .player import Player

BUTTONS = {"enum": ["left", "right", "middle"]}


# ---------------------------------------------------------------------------- timing


@S.step("hold", params={"seconds": {"type": "number", "minimum": 0}}, shorthand="seconds")
def hold(p: Player, params: dict):
    """Keep the current frame for a while (default 1 s)."""
    yield from p.seconds(params.get("seconds", params.get("duration", 1.0)))


@S.step(
    "wait",
    params={
        "seconds": {"type": "number", "minimum": 0},
        "idle": {"type": "boolean", "description": "Wait until background workers are done."},
        "until": {"type": "string", "description": "Python expression evaluated until it is true."},
        "timeout": {"type": "number", "minimum": 0},
        "show": {"type": "boolean", "description": "Record frames while waiting (default false)."},
    },
    shorthand="seconds",
)
def wait(p: Player, params: dict):
    """Wait (in real time) for the app: a fixed time, until idle, or until a condition holds.

    By default no frames are recorded while waiting, so long computations are cut out.
    """
    show = params.get("show", False)
    timeout = float(params.get("timeout", 30))
    secs = params.get("seconds")
    cond = params.get("until")
    if secs is None and not params.get("idle") and cond is None:
        params = {**params, "idle": True}
    start = time.perf_counter()

    def done() -> bool:
        if secs is not None and time.perf_counter() - start < secs:
            return False
        if params.get("idle") and not p.adapter.is_idle():
            return False
        return not (cond is not None and not eval(cond, p.namespace))  # noqa: S307

    if cond is not None and not p.allow_python:
        raise DemoError("'wait: until' runs Python; not allowed with --no-python")
    while not done():
        if time.perf_counter() - start > timeout:
            raise DemoError(f"wait: timed out after {timeout:g} s")
        QApplication.processEvents()
        if show:
            yield
        else:
            time.sleep(0.01)
    p.settle(0.05)
    yield


# ---------------------------------------------------------------------------- overlays


@S.step(
    "caption",
    params={
        "text": {"type": ["string", "null"]},
        "position": {"enum": ["top", "bottom"]},
    },
    shorthand="text",
)
def caption(p: Player, params: dict):
    """Show a caption (stays until the next caption; empty text hides it)."""
    text = params.get("text")
    if "position" in params:
        p.overlay.style.caption_position = params["position"]
    if text:
        p.overlay.caption = str(text)
        p.overlay.caption_t0 = p.t
        default = min(3.0, 0.9 + 0.035 * len(str(text)))
    else:
        p.overlay.caption = None
        default = 0.0
    yield from p.seconds(p.duration(params, default))


@S.step(
    "highlight",
    params={"label": {"type": "string"}, "move": {"type": "boolean", "description": "Point at it with the cursor."}},
    targets=True,
    shorthand="widget",
)
def highlight(p: Player, params: dict):
    """Draw a highlight box around a widget for ``duration`` seconds (default 1.5)."""
    widget = p.find(params)
    secs = p.duration(params, 1.5)
    p.add_highlight(widget, secs, params.get("label"))
    if params.get("move"):
        yield from p.move_to(p.widget_point(widget, params))
    yield from p.seconds(secs)


@S.step(
    "cursor",
    params={"visible": {"type": "boolean"}},
    targets=True,
    shorthand="visible",
)
def cursor(p: Player, params: dict):
    """Show / hide the cursor, or place it somewhere instantly."""
    if "visible" in params:
        p.overlay.style.cursor = bool(params["visible"])
    if p.has_target(params):
        pt = p.points(params)[0]
        p._set_pos(pt)
    yield


# ---------------------------------------------------------------------------- callouts

ANNOTATION_PARAMS = {
    "label": {"type": "string", "description": "Narrative text shown next to the callout."},
    "color": {"type": "string", "description": "Any Qt colour name or #hex (default: style palette)."},
    "hold": {
        "type": "boolean",
        "description": "true (default): wait 'duration' s. false: continue with the next steps while it shows.",
    },
    "keep": {"type": "boolean", "description": "Stay until clear_annotations instead of 'duration' s."},
}


def _annotate(p: Player, params: dict, kind: str, **extra):
    from .annotations import Annotation

    if not p.has_target(params):
        raise DemoError(f"{kind}: needs a target (button, widget, canvas or at)")
    secs = p.duration(params, 1.8)
    real = secs / (p.speed * p._step_speed)
    a = Annotation(
        kind=kind,
        anchor=p.anchor(params),
        t0=p.t,
        t1=None if params.get("keep") else p.t + real,
        label=params.get("label"),
        color=params.get("color"),
        **extra,
    )
    p.overlay.annotations.append(a)
    if params.get("hold", True):
        yield from p.seconds(secs)


@S.step(
    "arrow",
    params={
        **ANNOTATION_PARAMS,
        "from": {"enum": ["left", "right", "top", "bottom", "top-left", "top-right", "bottom-left", "bottom-right"],
                 "description": "Side the arrow comes from (default top-left)."},
        "length": {"type": "number", "minimum": 20, "description": "Arrow length in pixels (default 90)."},
    },
    targets=True,
    shorthand="widget",
)
def arrow(p: Player, params: dict):
    """Point an arrow at a widget or canvas point (with an optional label at its tail)."""
    yield from _annotate(p, params, "arrow", direction=params.get("from", "top-left"),
                         length=float(params.get("length", 90)))  # fmt: skip


@S.step(
    "step_circle",
    params={**ANNOTATION_PARAMS, "number": {"type": "integer", "minimum": 0, "description": "Default: previous + 1."}},
    targets=True,
    shorthand="widget",
)
def step_circle(p: Player, params: dict):
    """Put a numbered circle on a widget (top-left corner) or canvas point."""
    number = params.get("number", p.step_number + 1)
    p.step_number = number
    yield from _annotate(p, params, "step", number=number)


@S.step(
    "box",
    params={
        **ANNOTATION_PARAMS,
        "shape": {"enum": ["rect", "circle"]},
        "fill": {"type": "boolean", "description": "Tint the inside."},
    },
    targets=True,
    shorthand="widget",
)
def box(p: Player, params: dict):
    """Frame a widget or a canvas region (canvas: [[y0, x0], [y1, x1]]) with a box or circle."""
    yield from _annotate(p, params, "box", shape=params.get("shape", "rect"), fill=bool(params.get("fill", False)))


@S.step("clear_annotations", params={"reset_numbers": {"type": "boolean"}})
def clear_annotations(p: Player, params: dict):
    """Fade out all arrows, step circles and boxes (also ones with keep: true)."""
    for a in p.overlay.annotations:
        if a.t1 is None or a.t1 > p.t:
            a.t1 = p.t
    if params.get("reset_numbers"):
        p.step_number = 0
    yield from p.seconds(0.2)


# ---------------------------------------------------------------------------- mouse

CLICK_PARAMS = {
    "mouse": BUTTONS,
    "double": {"type": "boolean"},
    "highlight": {"type": ["boolean", "string"], "description": "Highlight the target (string: label)."},
    "keys": {"type": "string", "description": "Modifier keys held during the click, e.g. 'Shift'."},
}


@S.step("click", params=CLICK_PARAMS, targets=True, shorthand="button")
def click(p: Player, params: dict):
    """Move the cursor to a widget (or canvas point) and click it."""
    yield from _click(p, params, double=params.get("double", False))


@S.step("double_click", params=CLICK_PARAMS, targets=True, shorthand="button")
def double_click(p: Player, params: dict):
    """Double-click a widget or canvas point."""
    yield from _click(p, params, double=True)


def _click(p: Player, params: dict, double: bool):
    if not p.has_target(params):
        raise DemoError("click needs a target: button, widget, canvas or at")
    widget = None
    if "canvas" not in params and "at" not in params and "path" not in params:
        widget = p.find(params)
        point = p.widget_point(widget, params)
    else:
        point = p.points(params)[0]
    hl = None
    if params.get("highlight") and widget is not None:
        label = params["highlight"] if isinstance(params["highlight"], str) else None
        hl = p.add_highlight(widget, None, label)
    yield from p.move_to(point)
    with _modifiers(p, params.get("keys")):
        if params.get("keys"):
            p.show_keys(key_display(params["keys"]) + " + click")
        yield from p.click(point, params.get("mouse", "left"), double=double)
    if hl is not None:
        yield from p.seconds(0.3)
        p.end_highlight(hl)


class _modifiers:
    def __init__(self, p: Player, keys: str | None):
        self.p, self.keys = p, keys

    def __enter__(self):
        if self.keys:
            from .qt_compat import parse_key

            _, mods = parse_key(self.keys + "+A")
            self.p.input.modifiers = mods

    def __exit__(self, *exc):
        from .qt_compat import NO_MODIFIER

        self.p.input.modifiers = NO_MODIFIER


@S.step("move", params={}, targets=True, shorthand="button")
def move(p: Player, params: dict):
    """Move the cursor to a widget or point (hover)."""
    pts = p.points(params)
    yield from p.move_to(pts[0], params.get("duration"))
    yield from p.seconds(0.15)


@S.step(
    "drag",
    params={
        "mouse": BUTTONS,
        "to": {"type": "object", "description": "Target (same keys as a click) to drag to."},
        "keys": {"type": "string"},
    },
    targets=True,
)
def drag(p: Player, params: dict):
    """Press, move along a path (canvas / path / at) or to another target, release."""
    if "to" in params:
        start = p.points({k: v for k, v in params.items() if k != "to"})[0]
        end = p.points(params["to"])[0]
        pts = [start, end]
    else:
        pts = p.points(params)
    with _modifiers(p, params.get("keys")):
        if params.get("keys"):
            p.show_keys(key_display(params["keys"]) + " + drag")
        yield from p.drag(pts, params.get("mouse", "left"), params.get("duration"))


@S.step(
    "scroll",
    params={"steps": {"type": "number", "description": "Wheel notches; positive scrolls up / zooms in."}},
    targets=True,
)
def scroll(p: Player, params: dict):
    """Scroll the mouse wheel over a target (napari: zoom the canvas)."""
    if p.has_target(params):
        yield from p.move_to(p.points(params)[0])
    steps = float(params.get("steps", 1))
    n = max(1, round(abs(steps)))
    for _ in range(n):
        p.input.wheel(steps / n)
        yield from p.seconds(0.08)
    yield from p.seconds(0.2)


# ---------------------------------------------------------------------------- keyboard


@S.step(
    "key",
    params={
        "keys": {"type": "string", "description": "e.g. 'Ctrl+Z', 'Esc', 'P'"},
        "show": {"type": "boolean", "description": "Show the key badge (default true)."},
        "times": {"type": "integer", "minimum": 1},
    },
    required=["keys"],
    shorthand="keys",
)
def key(p: Player, params: dict):
    """Press a key combination (shown as a badge)."""
    for _ in range(params.get("times", 1)):
        if params.get("show", True):
            p.show_keys(key_display(params["keys"]))
        p.input.key(params["keys"])
        yield from p.seconds(0.25)
    yield from p.seconds(0.35)


@S.step(
    "type",
    params={
        "text": {"type": "string"},
        "enter": {"type": "boolean", "description": "Press Enter afterwards."},
        "clear": {"type": "boolean", "description": "Select all and replace (default: true if a target is given)."},
    },
    targets=True,
    required=["text"],
    shorthand="text",
)
def type_(p: Player, params: dict):
    """Type text (into a target widget, clicking it first, or the focused widget)."""
    if p.has_target(params):
        widget = p.find_input(params)
        yield from p.click(p.widget_point(widget, params))
        widget.setFocus()
        if params.get("clear", True):
            _select_all(widget)
    yield from _type_text(p, params["text"])
    if params.get("enter"):
        p.show_keys("Enter")
        p.input.key("Return")
        yield from p.seconds(0.3)


def _type_text(p: Player, text: str):
    for ch in text:
        p.input.type_char(ch)
        yield from p.seconds(p.rng.uniform(0.05, 0.12))


def _select_all(widget: QWidget) -> None:
    if isinstance(widget, QLineEdit | QAbstractSpinBox | QTextEdit | QPlainTextEdit):
        widget.selectAll()


# ---------------------------------------------------------------------------- set value


@S.step(
    "set",
    params={"value": {"description": "New value (number, text, item of a combo box, bool)."}},
    targets=True,
    required=["value"],
)
def set_(p: Player, params: dict):
    """Change a value widget like a person would: drag a slider, type into a field, pick a
    combo-box item, toggle a check box."""
    found = p.find(params)
    widget = _input_of(found)
    magic = _magic_of(widget, found)
    value = params["value"]
    hl = p.add_highlight(found, None) if params.get("highlight") else None
    if isinstance(widget, QAbstractSlider):
        yield from _set_slider(p, widget, value, magic)
    elif isinstance(widget, QComboBox):
        yield from _set_combo(p, widget, value)
    elif isinstance(widget, QAbstractButton) and widget.isCheckable():
        if widget.isChecked() != bool(value):
            yield from p.click(p.widget_point(widget))
    elif isinstance(widget, QAbstractSpinBox | QLineEdit | QTextEdit | QPlainTextEdit):
        yield from p.click(p.widget_point(widget))
        widget.setFocus()
        _select_all(widget)
        yield from p.seconds(0.1)
        yield from _type_text(p, _fmt(value))
        p.input.key("Return")
        yield from p.seconds(0.2)
        if not _has_value(widget, magic, value):
            p.direct_change(f"set: typing {_fmt(value)!r} left {_describe_value(widget, magic)!r}")
            _final_value(widget, magic, value)
    else:
        raise DemoError(f"set: don't know how to set a {type(widget).__name__}")
    if hl is not None:
        p.end_highlight(hl)
    yield from p.seconds(0.2)


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def _input_of(found: QWidget) -> QWidget:
    from .selectors import input_widget

    return input_widget(found)


def _magic_of(widget: QWidget, found: QWidget):
    """magicgui widget owning ``widget`` (if any), searching up to ``found``'s parent."""
    w = widget
    stop = found.parentWidget()
    while w is not None and w is not stop:
        mw = getattr(w, "_magic_widget", None)
        if mw is not None and hasattr(mw, "value"):
            return mw
        w = w.parentWidget()
    mw = getattr(found, "_magic_widget", None)
    return mw if mw is not None and hasattr(mw, "value") else None


def _current_value(widget: QWidget, magic):
    if magic is not None:
        return magic.value
    if isinstance(widget, QLineEdit):
        return widget.text()
    if isinstance(widget, QTextEdit | QPlainTextEdit):
        return widget.toPlainText()
    return widget.value() if hasattr(widget, "value") else None


def _describe_value(widget: QWidget, magic) -> str:
    try:
        return str(_current_value(widget, magic))
    except Exception:
        return "?"


def _has_value(widget: QWidget, magic, value, tol: float = 1e-6) -> bool:
    try:
        cur = _current_value(widget, magic)
    except Exception:
        return False
    if isinstance(value, bool) or isinstance(cur, bool):
        return bool(cur) == bool(value)
    if isinstance(value, int | float) and isinstance(cur, int | float):
        return abs(float(cur) - float(value)) <= tol + 1e-9
    return str(cur) == str(value) or norm(str(cur)) == norm(str(value))


def _final_value(widget: QWidget, magic, value) -> None:
    try:
        if magic is not None:
            magic.value = value
        elif isinstance(widget, QAbstractSlider | QAbstractSpinBox) and hasattr(widget, "setValue"):
            widget.setValue(value)
        elif isinstance(widget, QLineEdit) and widget.text() != str(value):
            widget.setText(str(value))
    except Exception:  # best effort - the visible interaction already happened
        pass


def _handle_point(p: Player, slider: QAbstractSlider, frac: float) -> QPoint:
    from .selectors import window_rect

    rect = window_rect(p.window, slider)
    horizontal = slider.width() >= slider.height()
    if isinstance(slider, QSlider):
        handle = slider.style().pixelMetric(QStyle.PixelMetric.PM_SliderLength, None, slider)
    else:  # scroll bars: arrow buttons + a handle of roughly page size
        handle = min(rect.height(), rect.width()) * 2.2
    if slider.invertedAppearance():
        frac = 1 - frac
    if horizontal:
        x = rect.left() + handle / 2 + frac * (rect.width() - handle)
        return QPoint(round(x), rect.center().y())
    y = rect.bottom() - handle / 2 - frac * (rect.height() - handle)
    return QPoint(rect.center().x(), round(y))


def _track_pixels(slider: QAbstractSlider) -> int:
    length = slider.width() if slider.width() >= slider.height() else slider.height()
    return max(1, length - 20)


def _set_slider(p: Player, widget: QAbstractSlider, value, magic):
    """Drag the handle like a person: one drag, then small corrections if it landed off target.

    The exact value is only snapped when the remaining error is below what one pixel of mouse
    movement can resolve; anything larger is a failed interaction (see ``Player.direct_change``).
    """
    # superqt / magicgui sliders wrap a plain QSlider: drag that one, read/set the outer value
    kids = [k for k in widget.findChildren(QAbstractSlider) if k.isVisible()]
    inner = kids[0] if kids else widget
    if magic is not None and hasattr(magic, "min") and hasattr(magic, "max"):
        lo, hi = float(magic.min), float(magic.max)
    else:
        lo, hi = float(widget.minimum()), float(widget.maximum())
    span = (hi - lo) or 1.0
    tol = span / _track_pixels(inner)  # one pixel's worth of value

    def frac(v: float) -> float:
        return min(1.0, max(0.0, (float(v) - lo) / span))

    target_f = frac(value)
    aim_f = target_f
    cur_f = frac(_current_value(widget, magic))
    for attempt in range(4):
        start = _handle_point(p, inner, cur_f)
        end = _handle_point(p, inner, aim_f)
        if attempt and (start - end).manhattanLength() < 1:
            break
        yield from p.drag([start, end], duration=(0.35 + 0.6 * abs(aim_f - cur_f)) if attempt == 0 else 0.3)
        got = float(_current_value(widget, magic))
        if abs(got - float(value)) <= tol:
            break
        # landed off target (handle geometry estimate): aim again, correcting by the error
        cur_f = frac(got)
        aim_f = min(1.0, max(0.0, aim_f + (target_f - cur_f)))
    if not _has_value(widget, magic, value, tol=tol):
        p.direct_change(
            f"set: dragging the slider ended at {_describe_value(widget, magic)} instead of {value}"
        )
    _final_value(widget, magic, value)  # only removes sub-pixel rounding at this point


def _set_combo(p: Player, combo: QComboBox, value):
    from .selectors import window_rect

    texts = [combo.itemText(i) for i in range(combo.count())]
    idx = next((i for i, t in enumerate(texts) if t == str(value)), None)
    if idx is None:
        idx = next((i for i, t in enumerate(texts) if norm(t) == norm(str(value))), None)
    if idx is None:
        raise DemoError(f"set: {value!r} is not an item of the combo box (items: {texts})")
    if idx == combo.currentIndex():
        yield from p.move_to(p.widget_point(combo))
        return
    yield from p.click(p.widget_point(combo))
    view = combo.view()
    yield from p.seconds(0.15)
    if view.isVisible():
        model_index = combo.model().index(idx, combo.modelColumn())
        view.scrollTo(model_index)
        r = view.visualRect(model_index)
        vp = view.viewport()
        top_left = vp.mapToGlobal(r.topLeft()) - p.window.mapToGlobal(QPoint(0, 0))
        target = top_left + QPoint(min(r.width() // 3, 60), r.height() // 2)
        vis = window_rect(p.window, vp)
        if not vis.contains(target):
            target = QPoint(vis.center().x(), max(vis.top() + 5, min(vis.bottom() - 5, target.y())))
        yield from p.click(target)
    if combo.currentIndex() != idx:
        if combo.view().isVisible():
            combo.hidePopup()
        p.direct_change(f"set: choosing {texts[idx]!r} in the combo box's list did not select it")
        combo.setCurrentIndex(idx)
        yield


# ---------------------------------------------------------------------------- menus


def _strip_mnemonic(text: str) -> str:
    return re.sub(r"&(?!&)", "", text).replace("&&", "&").split("\t")[0].strip()


def _match_action(actions, name):
    if isinstance(name, list):  # alternatives (labels differ between app versions)
        for alt in name:
            hit = _match_action(actions, alt)
            if hit is not None:
                return hit
        return None
    actions = [a for a in actions if not a.isSeparator() and a.isVisible()]
    for cmp in (lambda a: _strip_mnemonic(a.text()) == name,
                lambda a: _strip_mnemonic(a.text()).lower() == name.lower(),
                lambda a: norm(a.text()) == norm(name)):  # fmt: skip
        hits = [a for a in actions if cmp(a)]
        if hits:
            return hits[0]
    return None


@S.step(
    "menu",
    params={
        "path": {
            "type": "array",
            "items": {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "minItems": 1,
        }
    },
    required=["path"],
    shorthand="path",
)
def menu(p: Player, params: dict):
    """Open a menu-bar entry and click through sub-menus, e.g. [File, Open Sample, ...].

    A level can be a list of alternatives, e.g. [View, Scale Bar, [Toggle Scale Bar, Scale Bar
    Visible]] - the first existing one is used (labels change between app versions).
    """
    path = list(params["path"])
    bar = p.window.menuBar() if isinstance(p.window, QMainWindow) else p.window.findChild(QMenuBar)
    if bar is None:
        raise DemoError("menu: this window has no menu bar")
    act = _match_action(bar.actions(), path[0])
    if act is None:
        names = [_strip_mnemonic(a.text()) for a in bar.actions() if a.text()]
        raise DemoError(f"menu: no menu {path[0]!r} (menus: {names})")
    from .selectors import window_rect

    r = bar.actionGeometry(act)
    pt = window_rect(p.window, bar).topLeft() + r.center()
    yield from p.click(pt)
    current: QMenu | None = act.menu()
    for i, name in enumerate(path[1:], start=1):
        if current is None or not current.isVisible():
            if current is not None:
                current.popup(bar.mapToGlobal(r.bottomLeft()))
                yield from p.seconds(0.1)
            else:
                raise DemoError(f"menu: {path[i - 1]!r} has no sub-menu")
        QApplication.processEvents()
        act = _match_action(current.actions(), name)
        if act is None:
            names = [_strip_mnemonic(a.text()) for a in current.actions() if a.text() and not a.isSeparator()]
            _close_menus()
            raise DemoError(f"menu: no item {name!r} in {path[i - 1]!r} (items: {names})")
        ar = current.actionGeometry(act)
        local = QPoint(ar.left() + min(ar.width() // 3, 70), ar.center().y())
        pt = current.mapToGlobal(local) - p.window.mapToGlobal(QPoint(0, 0))
        yield from p.move_to(pt)
        yield from p.seconds(0.15)
        if act.menu() is not None:
            current.setActiveAction(act)
            sub = act.menu()
            yield from p.seconds(0.1)
            if not sub.isVisible():
                yield from p.click(pt)
            if not sub.isVisible():
                sub.popup(current.mapToGlobal(ar.topRight()))
            yield from p.seconds(0.15)
            current = sub
        else:
            yield from p.click(pt)
            if any(m.isVisible() for m in QApplication.topLevelWidgets() if isinstance(m, QMenu)):
                # the click did not trigger the item
                _close_menus()
                p.direct_change(f"menu: clicking {name!r} did not trigger it")
                act.trigger()
            current = None
    if current is not None:
        _close_menus()
    yield from p.seconds(0.2)


def _close_menus() -> None:
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMenu) and w.isVisible():
            w.close()


# ---------------------------------------------------------------------------- python


@S.step(
    "python",
    params={"code": {"type": "string", "description": "Inline code, or 'module:function'."}},
    required=["code"],
    shorthand="code",
    strict=False,
    direct=True,
    effect_label="code ran",
)
def python(p: Player, params: dict):
    """Run Python: inline code, or call ``module:function`` (extra keys become keyword args).

    The namespace contains the app objects (napari: ``viewer``), ``window`` and ``player``.
    """
    if not p.allow_python:
        raise DemoError("python steps are disabled (--no-python)")
    code = params["code"]
    extra = {k: v for k, v in params.items() if k not in ("code", "duration", "speed", "pause", "capture")}
    if re.fullmatch(r"[\w.]+:[\w.]+", code.strip()):
        from .adapter import import_object

        fn = import_object(code.strip(), p.spec.base_dir)
        kwargs = dict(extra)
        try:
            sig = inspect.signature(fn)
            accepts_any = any(prm.kind is prm.VAR_KEYWORD for prm in sig.parameters.values())
            for name in sig.parameters:
                if name in p.namespace and name not in kwargs:
                    kwargs[name] = p.namespace[name]
            if not accepts_any:
                kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        except (TypeError, ValueError):
            pass
        result = fn(**kwargs)
        if inspect.isgenerator(result):  # generator functions may yield frames themselves
            yield from result
    else:
        p.namespace.update(extra)
        exec(compile(code, f"<python step {p.current.location if p.current else ''}>", "exec"), p.namespace)  # noqa: S102
    yield from p.seconds(p.duration(params, 0.0))
