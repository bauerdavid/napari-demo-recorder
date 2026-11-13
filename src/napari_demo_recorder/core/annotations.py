"""Callout annotations: arrows, numbered step circles and boxes.

Unlike hand-drawn annotations, these are *anchored*: each one keeps a function that returns the
current rectangle of its target (a widget, or points on a canvas) and is re-evaluated every frame,
so callouts follow the UI when the layout, window size or camera changes.  They only paint on the
frames and never change the app, so they are allowed in strict demos.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from qtpy.QtCore import QPointF, QRectF, Qt
from qtpy.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

DRAW_IN = 0.3  # seconds for an annotation to draw itself in
FADE = 0.15

#: direction an arrow comes *from*, as a unit vector (x, y) in screen coordinates
DIRECTIONS = {
    "left": (-1.0, 0.0),
    "right": (1.0, 0.0),
    "top": (0.0, -1.0),
    "bottom": (0.0, 1.0),
    "top-left": (-0.7071, -0.7071),
    "top-right": (0.7071, -0.7071),
    "bottom-left": (-0.7071, 0.7071),
    "bottom-right": (0.7071, 0.7071),
}

#: high-contrast colour sets: (arrow, step circle, box)
PALETTES = {
    "accent": None,  # use style.accent for everything
    "warm": ("#ff5028", "#ffdc28", "#ff9f1c"),
    "cyan-magenta": ("#00ffff", "#ff00ff", "#00ffff"),
    "green": ("#00ff50", "#ffaa00", "#00ff50"),
    "white": ("#ffffff", "#ffffff", "#ffffff"),
    "black-yellow": ("#111111", "#ffeb00", "#111111"),
}

Anchor = Callable[[], "QRectF | None"]


@dataclass
class Annotation:
    kind: str  # "arrow" | "step" | "box"
    anchor: Anchor
    t0: float
    t1: float | None = None
    label: str | None = None
    color: str | None = None
    number: int | None = None
    direction: str = "top-left"
    length: float = 90.0
    shape: str = "rect"  # box: rect | circle
    fill: bool = False


def palette_color(palette: str, kind: str, accent: str) -> QColor:
    colors = PALETTES.get(palette)
    if colors is None:
        return QColor(accent)
    return QColor(colors[{"arrow": 0, "step": 1, "box": 2}[kind]])


def contrast_text(bg: QColor) -> QColor:
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return QColor(15, 15, 15) if luminance > 140 else QColor(255, 255, 255)


def paint_annotation(p: QPainter, a: Annotation, t: float, color: QColor) -> None:
    try:
        rect = a.anchor()
    except Exception:  # the target disappeared (widget deleted, layer removed)
        return
    if rect is None:
        return
    rect = QRectF(rect)
    grow = min(1.0, max(0.0, (t - a.t0) / DRAW_IN))
    grow = 1 - (1 - grow) ** 3  # ease out
    alpha = 1.0 if a.t1 is None else min(1.0, max(0.0, (a.t1 + FADE - t) / FADE))
    if alpha <= 0 or grow <= 0:
        return
    if a.color:
        color = QColor(a.color)
    if a.kind == "arrow":
        _arrow(p, a, rect, grow, alpha, color)
    elif a.kind == "step":
        _step(p, a, rect, grow, alpha, color)
    elif a.kind == "box":
        _box(p, a, rect, grow, alpha, color)


def _with_alpha(c: QColor, alpha: float) -> QColor:
    c = QColor(c)
    c.setAlphaF(c.alphaF() * alpha)
    return c


def _edge_point(rect: QRectF, d: tuple[float, float], gap: float) -> QPointF:
    """Point on the rectangle's border facing direction ``d`` (plus a small gap)."""
    c = rect.center()
    if rect.width() < 1 and rect.height() < 1:
        return QPointF(c.x() + d[0] * gap, c.y() + d[1] * gap)
    hw, hh = rect.width() / 2, rect.height() / 2
    sx = hw / abs(d[0]) if abs(d[0]) > 1e-6 else math.inf
    sy = hh / abs(d[1]) if abs(d[1]) > 1e-6 else math.inf
    s = min(sx, sy)
    return QPointF(c.x() + d[0] * (s + gap), c.y() + d[1] * (s + gap))


def _arrow(p: QPainter, a: Annotation, rect: QRectF, grow: float, alpha: float, color: QColor):
    d = DIRECTIONS.get(a.direction, DIRECTIONS["top-left"])
    tip = _edge_point(rect, d, 6)
    tail = QPointF(tip.x() + d[0] * a.length, tip.y() + d[1] * a.length)
    # the arrow grows from the tail towards the tip
    head_pos = QPointF(tail.x() + (tip.x() - tail.x()) * grow, tail.y() + (tip.y() - tail.y()) * grow)
    width = 5.0
    head_len, head_w = 18.0, 13.0
    ux, uy = -d[0], -d[1]  # pointing direction (tail -> tip)
    shaft_end = QPointF(head_pos.x() - ux * head_len * 0.6, head_pos.y() - uy * head_len * 0.6)
    head = QPainterPath()
    head.moveTo(head_pos)
    head.lineTo(QPointF(head_pos.x() - ux * head_len - uy * head_w / 2, head_pos.y() - uy * head_len + ux * head_w / 2))
    head.lineTo(QPointF(head_pos.x() - ux * head_len + uy * head_w / 2, head_pos.y() - uy * head_len - ux * head_w / 2))
    head.closeSubpath()
    # dark outline first so the arrow reads on light and dark backgrounds
    outline = _with_alpha(QColor(0, 0, 0, 170), alpha)
    p.setPen(QPen(outline, width + 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(tail, shaft_end)
    p.setPen(QPen(outline, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(outline)
    p.drawPath(head)
    c = _with_alpha(color, alpha)
    p.setPen(QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(tail, shaft_end)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawPath(head)
    if a.label and grow >= 1:
        _label(p, a.label, tail, d, color, alpha)


def _step(p: QPainter, a: Annotation, rect: QRectF, grow: float, alpha: float, color: QColor):
    r = 15.0 * (0.6 + 0.4 * grow) if grow < 1 else 15.0
    if rect.width() < 1 and rect.height() < 1:
        center = rect.center()
    else:  # sit on the target's top-left corner, like a badge
        center = QPointF(rect.left() - 4, rect.top() - 4)
    p.setPen(QPen(_with_alpha(QColor(0, 0, 0, 170), alpha), 3))
    p.setBrush(_with_alpha(color, alpha))
    p.drawEllipse(center, r, r)
    font = QFont()
    font.setPointSizeF(12.5)
    font.setBold(True)
    p.setFont(font)
    p.setPen(_with_alpha(contrast_text(color), alpha))
    p.drawText(QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, str(a.number or ""))
    if a.label and grow >= 1:
        _label(p, a.label, QPointF(center.x() + r + 4, center.y()), (1.0, 0.0), color, alpha)


def _box(p: QPainter, a: Annotation, rect: QRectF, grow: float, alpha: float, color: QColor):
    pad = 6.0
    r = rect.adjusted(-pad, -pad, pad, pad)
    if r.width() < 24:  # a single point: a small region around it
        r = QRectF(r.center().x() - 18, r.center().y() - 18, 36, 36)
    # draw in by scaling from the centre
    s = 0.85 + 0.15 * grow
    c = r.center()
    r = QRectF(c.x() - r.width() * s / 2, c.y() - r.height() * s / 2, r.width() * s, r.height() * s)
    p.setPen(QPen(_with_alpha(color, alpha * grow), 3.5))
    p.setBrush(_with_alpha(color, 0.18 * alpha * grow) if a.fill else Qt.BrushStyle.NoBrush)
    if a.shape == "circle":
        p.drawEllipse(r.adjusted(-r.width() * 0.1, -r.height() * 0.1, r.width() * 0.1, r.height() * 0.1))
    else:
        p.drawRoundedRect(r, 6, 6)
    if a.label and grow >= 1:
        _label(p, a.label, QPointF(r.left(), r.top() - 4), (0.0, -1.0), color, alpha)


def _label(p: QPainter, text: str, anchor: QPointF, d: tuple[float, float], color: QColor, alpha: float):
    """Narrative label next to ``anchor``, placed away from the target (direction ``d``)."""
    font = QFont()
    font.setPointSizeF(11.5)
    font.setBold(True)
    p.setFont(font)
    fm = QFontMetricsF(font)
    w, h = fm.horizontalAdvance(text) + 18, fm.height() + 10
    x = anchor.x() - w if d[0] < -0.3 else anchor.x() if d[0] > 0.3 else anchor.x() - w / 2
    y = anchor.y() - h if d[1] < -0.3 else anchor.y() if d[1] > 0.3 else anchor.y() - h / 2
    box = QRectF(x, y, w, h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_with_alpha(QColor(15, 15, 20, 230), alpha))
    p.drawRoundedRect(box, 6, 6)
    p.setPen(QPen(_with_alpha(color, alpha), 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(box, 6, 6)
    p.setPen(_with_alpha(QColor(255, 255, 255), alpha))
    p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
