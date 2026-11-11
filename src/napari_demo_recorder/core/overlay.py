"""Overlays painted onto every frame: cursor, click ripples, key badges, captions, highlights.

Xvfb (and most screen grabs) show no cursor and no key presses, so they are always composited.
All drawing goes through :func:`paint_overlays` so the rendered GIF and the live "play" preview
look identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from qtpy.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from qtpy.QtWidgets import QWidget

RIPPLE_TIME = 0.45
KEY_TIME = 1.1
FADE = 0.15


@dataclass
class Ripple:
    pos: QPoint
    t0: float
    button: str = "left"


@dataclass
class Highlight:
    t0: float
    t1: float | None
    widget: QWidget | None = None
    rect: QRect | None = None
    label: str | None = None


@dataclass
class OverlayStyle:
    cursor: bool = True
    cursor_scale: float = 1.0
    accent: str = "#ff9f1c"
    caption_position: str = "bottom"
    caption_size: float = 15.0
    keys_position: str = "bottom-right"

    @classmethod
    def from_dict(cls, d: dict | None) -> OverlayStyle:
        d = dict(d or {})
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class OverlayState:
    style: OverlayStyle = field(default_factory=OverlayStyle)
    cursor: QPoint | None = None
    pressed: bool = False
    ripples: list[Ripple] = field(default_factory=list)
    keys: list[tuple[str, float]] = field(default_factory=list)
    caption: str | None = None
    caption_t0: float = 0.0
    highlights: list[Highlight] = field(default_factory=list)

    def prune(self, t: float) -> None:
        self.ripples = [r for r in self.ripples if t - r.t0 < RIPPLE_TIME]
        self.keys = [k for k in self.keys if t - k[1] < KEY_TIME][-3:]
        self.highlights = [h for h in self.highlights if h.t1 is None or t < h.t1 + FADE]

    def active(self, t: float) -> bool:
        return bool(self.cursor or self.ripples or self.keys or self.caption or self.highlights)


def _fade(t: float, t0: float, t1: float | None) -> float:
    a = min(1.0, max(0.0, (t - t0) / FADE))
    if t1 is not None:
        a = min(a, max(0.0, (t1 + FADE - t) / FADE))
    return a


def paint_overlays(painter: QPainter, state: OverlayState, t: float, size, rect_of) -> None:
    """Paint all overlays.

    ``size`` is the frame size (QSize); ``rect_of(widget)`` maps a widget to a frame QRect.
    """
    state.prune(t)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    accent = QColor(state.style.accent)
    for h in state.highlights:
        rect = h.rect if h.rect is not None else (rect_of(h.widget) if h.widget is not None else None)
        if rect is not None:
            _paint_highlight(painter, QRectF(rect), accent, _fade(t, h.t0, h.t1), t - h.t0, h.label)
    for r in state.ripples:
        _paint_ripple(painter, r, t, accent)
    if state.caption:
        _paint_caption(painter, state, size, _fade(t, state.caption_t0, None))
    if state.keys:
        _paint_keys(painter, state, size, t)
    if state.cursor is not None and state.style.cursor:
        _paint_cursor(painter, QPointF(state.cursor), state.style.cursor_scale, state.pressed)


def _paint_highlight(p: QPainter, rect: QRectF, color: QColor, alpha: float, age: float, label: str | None):
    if alpha <= 0:
        return
    grow = 4 + 2.0 * (0.5 + 0.5 * math.sin(age * 6.0))
    r = rect.adjusted(-grow, -grow, grow, grow)
    fill = QColor(color)
    fill.setAlphaF(0.12 * alpha)
    pen_c = QColor(color)
    pen_c.setAlphaF(alpha)
    p.setPen(QPen(pen_c, 3))
    p.setBrush(fill)
    p.drawRoundedRect(r, 6, 6)
    if label:
        _paint_label(p, label, QPointF(r.left(), r.top() - 6), color, alpha)


def _paint_label(p: QPainter, text: str, anchor: QPointF, color: QColor, alpha: float):
    font = QFont()
    font.setPointSizeF(10.5)
    font.setBold(True)
    p.setFont(font)
    fm = QFontMetricsF(font)
    w, h = fm.horizontalAdvance(text) + 14, fm.height() + 6
    box = QRectF(anchor.x(), anchor.y() - h, w, h)
    bg = QColor(color)
    bg.setAlphaF(0.95 * alpha)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg)
    p.drawRoundedRect(box, 4, 4)
    p.setPen(QColor(20, 20, 20, int(255 * alpha)))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def _paint_ripple(p: QPainter, r: Ripple, t: float, color: QColor):
    age = (t - r.t0) / RIPPLE_TIME
    if age < 0 or age > 1:
        return
    radius = 6 + 22 * age
    c = QColor(color)
    c.setAlphaF(0.9 * (1 - age))
    p.setPen(QPen(c, 3))
    fill = QColor(color)
    fill.setAlphaF(0.25 * (1 - age))
    p.setBrush(fill)
    p.drawEllipse(QPointF(r.pos), radius, radius)


def _paint_caption(p: QPainter, state: OverlayState, size, alpha: float):
    font = QFont()
    font.setPointSizeF(state.style.caption_size)
    font.setBold(True)
    p.setFont(font)
    fm = QFontMetricsF(font)
    text = state.caption or ""
    lines = text.split("\n")
    tw = max(fm.horizontalAdvance(line) for line in lines)
    th = fm.lineSpacing() * len(lines)
    pad_x, pad_y = 22, 12
    w, h = tw + 2 * pad_x, th + 2 * pad_y
    x = (size.width() - w) / 2
    y = 18 if state.style.caption_position == "top" else size.height() - h - 24
    box = QRectF(x, y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(15, 15, 20, int(215 * alpha)))
    p.drawRoundedRect(box, 10, 10)
    p.setPen(QColor(255, 255, 255, int(255 * alpha)))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


def _paint_keys(p: QPainter, state: OverlayState, size, t: float):
    font = QFont()
    font.setPointSizeF(16)
    font.setBold(True)
    p.setFont(font)
    fm = QFontMetricsF(font)
    pos = state.style.keys_position
    y = size.height() - 30 if pos.startswith("bottom") else 30
    # stack badges, newest closest to the edge
    for i, (text, t0) in enumerate(reversed(state.keys)):
        age = t - t0
        alpha = min(1.0, age / 0.08) * min(1.0, max(0.0, (KEY_TIME - age) / 0.3))
        w, h = fm.horizontalAdvance(text) + 30, fm.height() + 16
        if pos.endswith("left"):
            x = 24
        elif pos.endswith("right"):
            x = size.width() - w - 24
        else:
            x = (size.width() - w) / 2
        dy = i * (h + 8)
        top = (y - h - dy) if pos.startswith("bottom") else (y + dy)
        box = QRectF(x, top, w, h)
        p.setPen(QPen(QColor(255, 255, 255, int(200 * alpha)), 2))
        p.setBrush(QColor(25, 25, 30, int(225 * alpha)))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor(255, 255, 255, int(255 * alpha)))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


_ARROW = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.5), (9.6, 18.4), (6.9, 12.3), (12.2, 12.3)]


def _paint_cursor(p: QPainter, pos: QPointF, scale: float, pressed: bool):
    s = 1.15 * scale * (0.92 if pressed else 1.0)
    path = QPainterPath()
    path.moveTo(pos + QPointF(_ARROW[0][0] * s, _ARROW[0][1] * s))
    for x, y in _ARROW[1:]:
        path.lineTo(pos + QPointF(x * s, y * s))
    path.closeSubpath()
    # soft shadow
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, 60))
    p.drawPath(path.translated(1.5, 2))
    p.setPen(QPen(QColor(255, 255, 255), 1.6))
    p.setBrush(QColor(15, 15, 15))
    p.drawPath(path)
