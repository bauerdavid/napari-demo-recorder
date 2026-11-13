"""Running a demo: virtual clock, motion helpers and frame capture.

Rendering is deterministic: the clock advances exactly ``1/fps`` per frame regardless of how long
grabbing takes, and all "human" jitter comes from a seeded RNG.
"""

from __future__ import annotations

import math
import random
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from qtpy.QtCore import QPoint, QPointF, Qt
from qtpy.QtGui import QPainter
from qtpy.QtWidgets import QApplication, QWidget

from . import builtin_steps  # noqa: F401  (registers the core steps)
from .adapter import Adapter, adapter_class
from .capture import FrameStore, compose_frame
from .encode import DEFAULT_MAX_SIZE, EncodeResult, encode_gif, encode_mp4
from .format import DemoError, DemoSpec, Step, parse_size
from .input import VirtualInput
from .overlay import EffectBadge, Highlight, OverlayState, OverlayStyle, Ripple, paint_overlays
from .selectors import INTERNAL_PREFIX, find_widget, input_widget, window_center, window_rect
from .steps import CORE_STEPS

DEFAULT_SIZE = (1280, 800)
DEFAULT_FPS = 15


def ease(t: float) -> float:
    """Minimum-jerk profile (what human pointing movements look like)."""
    t = min(1.0, max(0.0, t))
    return t * t * t * (10 - 15 * t + 6 * t * t)


class _OverlayWidget(QWidget):
    """Paints the overlays on top of the live window in ``play`` mode."""

    def __init__(self, player: Player):
        super().__init__(player.window)
        self.player = player
        self.setObjectName(INTERNAL_PREFIX + "overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(player.window.rect())
        self.show()
        self.raise_()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        w = self.player.window
        paint_overlays(p, self.player.overlay, self.player.t, self.size(), lambda x: window_rect(w, x))
        p.end()


class Player:
    """Runs a :class:`DemoSpec`.

    ``mode``: ``"render"`` (capture frames, encode), ``"play"`` (real-time preview with live
    overlays, nothing written) or ``"run"`` (as fast as possible, nothing captured; for tests).
    """

    def __init__(
        self,
        spec: DemoSpec,
        *,
        mode: str = "render",
        allow_python: bool = True,
        log: Callable[[str], Any] | None = print,
        adapter: Adapter | None = None,
    ):
        self.spec = spec
        self.mode = mode
        self.allow_python = allow_python
        self.log = log or (lambda *_: None)
        self.registry = CORE_STEPS.merged(adapter_class(spec.app_name).steps)
        self.adapter = adapter
        self._owns_app = adapter is None
        out = spec.output
        self.fps = float(out.get("fps", DEFAULT_FPS))
        self.speed = float(spec.data.get("speed", 1.0))
        self.rng = random.Random(spec.data.get("seed", 0))
        self.overlay = OverlayState(style=OverlayStyle.from_dict(spec.style))
        self.store = FrameStore(self.fps)
        self.t = 0.0
        self.capturing = False
        self._step_speed = 1.0
        self._wall_start = 0.0
        self._overlay_widget: _OverlayWidget | None = None
        self.window: QWidget | None = None
        self.input: VirtualInput | None = None
        self.namespace: dict[str, Any] = {}
        self.current: Step | None = None
        self.strict = spec.strict
        self.step_number = 0  # last number used by step_circle
        self.section = "setup"
        self._effect: EffectBadge | None = None

    # ================================================================== lifecycle
    def start(self) -> QWidget:
        if self.adapter is None:
            self.adapter = adapter_class(self.spec.app_name)(self.spec.app, self.spec.base_dir)
            window = self.adapter.start(self.spec.window)
        else:
            window = self.adapter.window
        self.window = window
        if self.mode == "render":
            # a blinking text caret changes pixels in every frame -> huge GIFs; keep it steady
            QApplication.setCursorFlashTime(0)
        size = self.spec.window.get("size", DEFAULT_SIZE)
        window.resize(int(size[0]), int(size[1]))
        window.show()
        window.raise_()
        window.activateWindow()
        if hasattr(QApplication, "setActiveWindow"):
            QApplication.setActiveWindow(window)
        self.settle(0.6)
        self.input = VirtualInput(window, self.adapter)
        self.overlay.cursor = QPoint(int(window.width() * 0.62), int(window.height() * 0.55))
        self.input.pos = QPoint(self.overlay.cursor)
        if self.mode == "play":
            self._overlay_widget = _OverlayWidget(self)
        self.namespace = {**self.adapter.namespace(), "player": self, "np": _np()}
        if str(self.spec.base_dir) not in sys.path:
            sys.path.insert(0, str(self.spec.base_dir))
        return window

    def settle(self, seconds: float = 0.2) -> None:
        """Process events in real time (lets the app finish layouting / redrawing)."""
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def run(self, output: str | Path | None = None, *, close: bool = True) -> EncodeResult | None:
        try:
            self.start()
            self.capturing = False
            for i, step in enumerate(self.spec.setup):
                self.run_step(step, index=i, total=len(self.spec.setup), section="setup")
            self.settle(0.3)
            self.capturing = self.mode != "run"
            self._wall_start = time.perf_counter()
            self.frame()  # first frame: initial state
            for i, step in enumerate(self.spec.steps):
                self.run_step(step, index=i, total=len(self.spec.steps), section="steps")
            for _ in self.seconds(float(self.spec.output.get("end_hold", 1.2)), scaled=False):
                self.frame()
            self.capturing = False
            if self.mode == "render":
                return self.encode(output)
            return None
        finally:
            if close and self._owns_app and self.adapter is not None:
                self.adapter.close()

    def encode(self, output: str | Path | None = None) -> EncodeResult:
        out = self.spec.output
        path = self.spec.output_path(output)
        fmt = path.suffix.lstrip(".").lower() or out.get("format", "gif")
        self.log(f"encoding {len(self.store)} unique frames ({self.store.duration:.1f} s) -> {path}")
        if fmt == "mp4":
            result = encode_mp4(self.store, path, scale=float(out.get("scale", 1.0)), log=self.log)
        else:
            max_size = parse_size(out["max_size"]) if "max_size" in out else DEFAULT_MAX_SIZE
            result = encode_gif(
                self.store,
                path,
                scale=float(out.get("scale", 1.0)),
                fps=self.fps,
                colors=int(out.get("colors", 256)),
                loop=bool(out.get("loop", True)),
                max_size=max_size,
                log=self.log,
            )
        self.log(result.summary())
        return result

    # ================================================================== steps
    def run_step(self, step: Step, *, index: int | None = None, total: int = 0, section: str = "steps") -> None:
        st = self.registry.get(step.name)
        if st is None:
            raise DemoError(f"unknown step {step.name!r}", location=step.location)
        params = step.params
        where = f"[{section} {index + 1}/{total}] " if index is not None else ""
        self.log(f"{where}{step.name} {_short_params(params)}")
        prev_capturing, prev_speed = self.capturing, self._step_speed
        if params.get("capture") is False:
            self.capturing = False
        self._step_speed = float(params.get("speed", 1.0))
        self.current = step
        self.section = section
        self._effect = None
        t_start = self.t
        try:
            if section == "steps" and (st.direct or params.get("effect")):
                self.mark_effect(_effect_text(params, st.effect_label))
            for _ in st.run(self, params):
                self.frame()
            if self._effect is not None:
                # keep the marker readable even for instant changes (python, camera jumps)
                for _ in self.seconds(max(0.0, 1.2 - (self.t - t_start)), scaled=False):
                    self.frame()
                self._effect.t1 = self.t
                self._effect = None
            if params.get("pause"):
                for _ in self.seconds(params["pause"]):
                    self.frame()
        except DemoError as err:
            if err.location:
                raise
            raise DemoError(str(err), location=step.location) from None
        except Exception as err:
            raise DemoError(f"{step.name} failed: {type(err).__name__}: {err}", location=step.location) from err
        finally:
            self.capturing, self._step_speed = prev_capturing, prev_speed
            self.current = None

    # ================================================================== honesty
    def mark_effect(self, text: str) -> None:
        """Show the "this did not come from user input" marker for the current step."""
        if self._effect is None:
            self._effect = EffectBadge(text, self.t)
            self.overlay.effects.append(self._effect)

    def direct_change(self, what: str) -> None:
        """Gate for any change that is not caused by simulated input.

        Allowed in ``setup``, in steps marked with ``effect`` and in relaxed demos - always with
        a visible marker.  In strict mode it is an error: the demo would show something a viewer
        cannot reproduce (usually the interaction silently failed, e.g. after a UI change).
        """
        if self.section == "setup":
            return
        params = self.current.params if self.current is not None else {}
        if self.strict and not params.get("effect"):
            raise DemoError(
                f"{what}. In strict mode a step may only change the app through visible input; "
                "fix the step (the UI may have changed), or mark it with 'effect: <what happened>' "
                "to show a marker instead."
            )
        st = self.registry.get(self.current.name) if self.current is not None else None
        self.mark_effect(_effect_text(params, st.effect_label if st else "changed directly"))

    # ================================================================== clock & frames
    @property
    def dt(self) -> float:
        return 1.0 / self.fps

    def frame(self) -> None:
        """Advance the clock by one frame: process events, capture / display."""
        QApplication.processEvents()
        if self.capturing or self.mode == "play":
            self.adapter.prepare_frame()
        if self.capturing and self.mode == "render":
            self.store.append(compose_frame(self.window, self.overlay, self.t))
        elif self.mode == "play" and self.capturing:
            if self._overlay_widget is not None:
                self._overlay_widget.setGeometry(self.window.rect())
                self._overlay_widget.raise_()
                self._overlay_widget.update()
            QApplication.processEvents()
            delay = self._wall_start + self.t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        if self.capturing:
            self.t += self.dt

    def seconds(self, s: float, *, scaled: bool = True) -> Iterator[None]:
        if scaled:
            s = s / (self.speed * self._step_speed)
        n = max(0, round(s * self.fps)) if self.capturing else (1 if s > 0 else 0)
        for _ in range(n):
            yield

    def duration(self, params: dict, default: float) -> float:
        return float(params.get("duration", default))

    def n_frames(self, seconds: float) -> int:
        if not self.capturing:
            return 1
        return max(1, round(seconds / (self.speed * self._step_speed) * self.fps))

    # ================================================================== targets
    def find(self, params: dict) -> QWidget:
        return find_widget(self.window, params, self.adapter)

    def find_input(self, params: dict) -> QWidget:
        return input_widget(self.find(params))

    def points(self, params: dict) -> list[QPoint]:
        """Window-coordinate point(s) a step refers to."""
        if "canvas" in params or "path" in params:
            pts = params.get("canvas", params.get("path"))
            if pts and not isinstance(pts[0], list | tuple):
                pts = [pts]
            return self.adapter.canvas_points(self, pts, params)
        if "at" in params:
            pts = params["at"]
            if pts and not isinstance(pts[0], list | tuple):
                pts = [pts]
            return [QPoint(round(p[0]), round(p[1])) for p in pts]
        widget = self.find(params)
        return [self.widget_point(widget, params)]

    def widget_point(self, widget: QWidget, params: dict | None = None) -> QPoint:
        p = window_center(self.window, widget)
        off = (params or {}).get("offset")
        if off:
            p = p + QPoint(round(off[0]), round(off[1]))
        return p

    def anchor(self, params: dict):
        """A function returning the target's current rectangle (window coords) for annotations.

        Widgets are looked up once and re-measured every frame; canvas points are re-mapped
        every frame, so callouts follow layout and camera changes.
        """
        from qtpy.QtCore import QRectF

        if any(k in params for k in ("canvas", "at", "path")):
            def points_rect():
                pts = self.points(params)
                xs = [q.x() for q in pts]
                ys = [q.y() for q in pts]
                return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

            points_rect()  # fail early (bad coordinates / missing layer)
            return points_rect
        widget = self.find(params)
        window = self.window

        def widget_rect():
            try:
                if not widget.isVisible():
                    return None
                return QRectF(window_rect(window, widget))
            except RuntimeError:  # deleted
                return None

        return widget_rect

    def has_target(self, params: dict) -> bool:
        return any(k in params for k in ("button", "widget", "canvas", "at", "path", "class", "object_name", "tooltip"))

    # ================================================================== motion
    def move_to(self, target: QPoint, duration: float | None = None) -> Iterator[None]:
        start = QPointF(self.input.pos)
        end = QPointF(target)
        dist = math.hypot(end.x() - start.x(), end.y() - start.y())
        if dist < 1:
            return
        if duration is None:
            duration = 0.22 + 0.11 * math.log2(1 + dist / 18)
        n = self.n_frames(duration)
        # slightly curved path, like a wrist movement
        bend = self.rng.uniform(-0.12, 0.12) * dist
        nx, ny = -(end.y() - start.y()) / dist, (end.x() - start.x()) / dist
        ctrl = QPointF((start.x() + end.x()) / 2 + nx * bend, (start.y() + end.y()) / 2 + ny * bend)
        for i in range(1, n + 1):
            u = ease(i / n)
            x = (1 - u) ** 2 * start.x() + 2 * (1 - u) * u * ctrl.x() + u * u * end.x()
            y = (1 - u) ** 2 * start.y() + 2 * (1 - u) * u * ctrl.y() + u * u * end.y()
            self._set_pos(QPoint(round(x), round(y)))
            yield
        self._set_pos(QPoint(target))

    def _set_pos(self, p: QPoint) -> None:
        self.input.move(p)
        self.overlay.cursor = QPoint(p)

    def click(self, point: QPoint, button: str = "left", double: bool = False) -> Iterator[None]:
        yield from self.move_to(point)
        yield from self.seconds(0.08)
        for i in range(2 if double else 1):
            if i == 1:
                self.input.double_click_event(button)
            else:
                self.input.press(button)
            self.overlay.pressed = True
            self.overlay.ripples.append(Ripple(QPoint(point), self.t, button))
            yield from self.seconds(0.07)
            self.input.release(button)
            self.overlay.pressed = False
            yield from self.seconds(0.06)
        yield from self.seconds(0.25)

    def drag(self, points: list[QPoint], button: str = "left", duration: float | None = None) -> Iterator[None]:
        if not points:
            return
        yield from self.move_to(points[0])
        yield from self.seconds(0.08)
        self.input.press(button)
        self.overlay.pressed = True
        self.overlay.ripples.append(Ripple(QPoint(points[0]), self.t, button))
        yield from self.seconds(0.08)
        fast = not self.capturing
        if fast:
            # without frame grabbing there is no real time between events; apps that handle the
            # press or moves on timers (napari) would miss parts of the drag
            self.settle(0.03)
        path = [QPointF(p) for p in points]
        if len(path) == 1:
            path.append(path[0])
        seg = [math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(path, path[1:])]
        total = sum(seg) or 1.0
        if duration is None:
            duration = min(5.0, max(0.4, total / 450))
        n = self.n_frames(duration)
        if fast:
            n = max(n, len(path) * 2, 10)  # still send intermediate moves when not capturing
        for i in range(1, n + 1):
            d = (0.15 * (i / n) + 0.85 * ease(i / n)) * total
            self._set_pos(_along(path, seg, d))
            if fast:
                self.settle(0.01)
            yield
        self._set_pos(points[-1])
        yield from self.seconds(0.06)
        # Apps may throttle mouse-move handling with timers (napari does); give pending moves
        # real time to be delivered before the release, or the end of the drag is lost.
        self.settle(0.05)
        self.input.release(button)
        self.overlay.pressed = False
        yield from self.seconds(0.2)

    # ================================================================== overlays
    def show_keys(self, text: str) -> None:
        self.overlay.keys.append((text, self.t))

    def add_highlight(self, widget: QWidget | None, seconds: float | None, label: str | None = None) -> Highlight:
        t1 = None if seconds is None else self.t + seconds / (self.speed * self._step_speed)
        h = Highlight(t0=self.t, t1=t1, widget=widget, label=label)
        self.overlay.highlights.append(h)
        return h

    def end_highlight(self, h: Highlight) -> None:
        h.t1 = self.t


def _along(path: list[QPointF], seg: list[float], d: float) -> QPoint:
    for a, b, length in zip(path, path[1:], seg):
        if d <= length or length == 0 and d <= 0:
            f = 0 if length == 0 else d / length
            return QPoint(round(a.x() + (b.x() - a.x()) * f), round(a.y() + (b.y() - a.y()) * f))
        d -= length
    return path[-1].toPoint()


def _effect_text(params: dict, default: str) -> str:
    effect = params.get("effect")
    return effect if isinstance(effect, str) and effect.strip() else default


def _short_params(params: dict) -> str:
    txt = ", ".join(f"{k}={v!r}" for k, v in params.items() if k not in ("code",))
    return txt if len(txt) < 90 else txt[:87] + "..."


def _np():
    import numpy

    return numpy
