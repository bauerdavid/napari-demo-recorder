"""Small helpers that hide Qt5 / Qt6 (PyQt / PySide) differences."""

from __future__ import annotations

import numpy as np
from qtpy import QT6
from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
from qtpy.QtGui import QImage, QKeySequence, QMouseEvent, QWheelEvent

MOUSE_BUTTONS = {
    "left": Qt.MouseButton.LeftButton,
    "right": Qt.MouseButton.RightButton,
    "middle": Qt.MouseButton.MiddleButton,
}

MODIFIER_NAMES = [
    (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
    (Qt.KeyboardModifier.AltModifier, "Alt"),
    (Qt.KeyboardModifier.ShiftModifier, "Shift"),
    (Qt.KeyboardModifier.MetaModifier, "Meta"),
]

NO_MODIFIER = Qt.KeyboardModifier.NoModifier


def as_int(flag) -> int:
    """Integer value of a Qt enum / flag in every binding."""
    try:
        return int(flag)
    except TypeError:
        return int(flag.value)


def qpoint(p) -> QPoint:
    if isinstance(p, QPoint):
        return p
    if isinstance(p, QPointF):
        return p.toPoint()
    return QPoint(round(p[0]), round(p[1]))


def mouse_event(kind: QEvent.Type, local, global_, button, buttons, modifiers=NO_MODIFIER):
    local = QPointF(qpoint(local))
    global_ = QPointF(qpoint(global_))
    return QMouseEvent(kind, local, global_, global_, button, buttons, modifiers)


def wheel_event(local, global_, angle_delta: QPoint, buttons, modifiers=NO_MODIFIER):
    return QWheelEvent(
        QPointF(qpoint(local)),
        QPointF(qpoint(global_)),
        QPoint(0, 0),
        angle_delta,
        buttons,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def event_pos(event) -> QPoint:
    """Widget-local position of a mouse/wheel event."""
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


def event_global_pos(event) -> QPoint:
    if hasattr(event, "globalPosition"):
        return event.globalPosition().toPoint()
    return event.globalPos()


def parse_key(text: str) -> tuple[int, Qt.KeyboardModifier]:
    """Parse "Ctrl+Shift+Z" / "Esc" / "I" into (Qt key, modifiers)."""
    aliases = {"esc": "Escape", "enter": "Return", "del": "Delete", "ctrl": "Ctrl", "cmd": "Ctrl"}
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts and "+" in text:
        parts = ["+"]
    parts = [aliases.get(p.lower(), p) for p in parts]
    seq = QKeySequence("+".join(parts))
    if seq.count() == 0:
        raise ValueError(f"Unknown key: {text!r}")
    combo = seq[0]
    if hasattr(combo, "key"):  # Qt6 QKeyCombination
        return as_int(combo.key()), combo.keyboardModifiers()
    combo = int(combo)
    mod_mask = 0xFE000000
    return combo & ~mod_mask, Qt.KeyboardModifier(combo & mod_mask)


def key_display(text: str) -> str:
    """Human-friendly label for a key-press badge, e.g. "Ctrl + Shift + Z"."""
    key, mods = parse_key(text)
    names = [name for flag, name in MODIFIER_NAMES if as_int(mods) & as_int(flag)]
    key_name = QKeySequence(key).toString() or text
    names.append({"Return": "Enter", "Escape": "Esc"}.get(key_name, key_name))
    return " + ".join(names)


def key_text(key: int, mods) -> str:
    """Text that a key press would produce (for QKeyEvent.text)."""
    if as_int(mods) & (as_int(Qt.KeyboardModifier.ControlModifier) | as_int(Qt.KeyboardModifier.AltModifier)):
        return ""
    if 0x20 <= key <= 0x7E:
        ch = chr(key)
        return ch if as_int(mods) & as_int(Qt.KeyboardModifier.ShiftModifier) else ch.lower()
    return {as_int(Qt.Key.Key_Return): "\r", as_int(Qt.Key.Key_Enter): "\r", as_int(Qt.Key.Key_Tab): "\t"}.get(key, "")


def to_key_enum(key: int):
    return Qt.Key(key)


def qimage_to_array(image: QImage) -> np.ndarray:
    """Convert a QImage to an (H, W, 3) uint8 RGB array."""
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = image.width(), image.height()
    ptr = image.constBits()
    nbytes = image.sizeInBytes() if hasattr(image, "sizeInBytes") else image.byteCount()
    if hasattr(ptr, "setsize"):
        ptr.setsize(nbytes)
    arr = np.frombuffer(ptr, np.uint8, count=nbytes).reshape(h, image.bytesPerLine())
    return arr[:, : w * 4].reshape(h, w, 4)[..., :3].copy()


def array_to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr[..., :3])
    h, w, _ = arr.shape
    img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return img.copy()


__all__ = [
    "QT6",
    "MOUSE_BUTTONS",
    "as_int",
    "qpoint",
    "mouse_event",
    "wheel_event",
    "event_pos",
    "event_global_pos",
    "parse_key",
    "key_display",
    "key_text",
    "qimage_to_array",
    "array_to_qimage",
]
