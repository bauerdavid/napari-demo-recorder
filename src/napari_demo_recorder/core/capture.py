"""Grabbing frames and keeping them (compressed) in memory."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import numpy as np
from qtpy.QtCore import QPoint
from qtpy.QtGui import QImage, QPainter
from qtpy.QtWidgets import QWidget

from .overlay import OverlayState, paint_overlays
from .qt_compat import qimage_to_array
from .selectors import popups, window_rect


def grab_window(window: QWidget) -> QImage:
    """The window plus any visible popup (menus, combo lists) at the right place."""
    image = window.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1.0)
    dpr = window.devicePixelRatioF()
    if dpr != 1.0:
        image = image.scaled(window.width(), window.height())
    ops = [p for p in popups() if p is not window and not window.isAncestorOf(p)]
    if ops:
        painter = QPainter(image)
        for popup in reversed(ops):  # bottom-most first
            pix = popup.grab()
            pix.setDevicePixelRatio(1.0)
            r = window_rect(window, popup)
            painter.drawPixmap(r.topLeft(), pix.scaled(r.size()) if dpr != 1.0 else pix)
        painter.end()
    return image


def compose_frame(window: QWidget, overlay: OverlayState | None, t: float) -> np.ndarray:
    image = grab_window(window)
    if overlay is not None and overlay.active(t):
        painter = QPainter(image)
        paint_overlays(painter, overlay, t, image.size(), lambda w: window_rect(window, w))
        painter.end()
    return qimage_to_array(image)


@dataclass
class FrameStore:
    """Frames kept zlib-compressed; identical consecutive frames are merged."""

    fps: float
    shape: tuple[int, int, int] | None = None
    _data: list[bytes] = field(default_factory=list)
    _durations: list[float] = field(default_factory=list)
    _last: np.ndarray | None = None

    def append(self, frame: np.ndarray, duration: float | None = None) -> None:
        duration = 1.0 / self.fps if duration is None else duration
        if self.shape is None:
            self.shape = frame.shape
        elif frame.shape != self.shape:
            frame = _fit(frame, self.shape)
        if self._last is not None and np.array_equal(frame, self._last):
            self._durations[-1] += duration
            return
        self._last = frame
        self._data.append(zlib.compress(frame.tobytes(), 1))
        self._durations.append(duration)

    def hold(self, seconds: float) -> None:
        if self._durations:
            self._durations[-1] += seconds

    def __len__(self) -> int:
        return len(self._data)

    @property
    def duration(self) -> float:
        return float(sum(self._durations))

    def frames(self):
        """Yield (array, duration_seconds)."""
        for data, dur in zip(self._data, self._durations):
            yield np.frombuffer(zlib.decompress(data), np.uint8).reshape(self.shape), dur

    def last(self) -> np.ndarray | None:
        return self._last


def _fit(frame: np.ndarray, shape) -> np.ndarray:
    """Pad / crop a frame to ``shape`` (window resized during a demo)."""
    out = np.zeros(shape, np.uint8)
    h, w = min(shape[0], frame.shape[0]), min(shape[1], frame.shape[1])
    out[:h, :w] = frame[:h, :w]
    return out


__all__ = ["grab_window", "compose_frame", "FrameStore", "QPoint"]
