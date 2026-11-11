"""Differences between napari versions (0.5 ... latest), kept in one place.

We avoid napari's private attributes where a widget-tree lookup works; where we own the viewer
(rendering) the private ``_qt_window`` is used with a fallback.
"""

from __future__ import annotations

from qtpy.QtWidgets import QApplication, QMainWindow, QWidget


def napari_version() -> tuple[int, ...]:
    import napari

    parts = []
    for p in napari.__version__.split(".")[:3]:
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def qt_window(viewer) -> QMainWindow:
    win = getattr(viewer.window, "_qt_window", None)
    if win is not None:
        return win
    # fallback: the main window that hosts the canvas
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QMainWindow) and canvas_widget(w) is not None:
            return w
    raise RuntimeError("cannot find napari's main window")


def canvas_widget(window: QWidget) -> QWidget | None:
    for w in window.findChildren(QWidget):
        if type(w).__name__.startswith("CanvasBackend"):
            return w
    return None


NOTIFICATION_CLASSES = ("NapariQtNotification",)


def hide_notifications(window: QWidget) -> None:
    for w in window.findChildren(QWidget):
        if type(w).__name__ in NOTIFICATION_CLASSES and w.isVisible():
            w.hide()
    for w in QApplication.topLevelWidgets():
        if type(w).__name__ in NOTIFICATION_CLASSES and w.isVisible():
            w.hide()


def layer_names_in_list(view) -> list[str]:
    m = view.model()
    return [str(m.data(m.index(r, 0))) for r in range(m.rowCount())]


def close_viewer(viewer) -> None:
    """Close a viewer without leaving queued status updates behind.

    napari's status checker thread posts queued signals; if they are delivered after the window
    closed, napari (0.5.x) raises AttributeError in a Qt callback.  Stop the thread and flush the
    queue while the window still exists, then close.
    """
    qtw = getattr(viewer.window, "_qt_window", None)
    thread = getattr(qtw, "status_thread", None)
    if thread is not None:
        try:
            thread.terminate()
            thread.wait(2000)
        except RuntimeError:
            pass
    for _ in range(3):
        QApplication.processEvents()
    viewer.close()
    QApplication.processEvents()
