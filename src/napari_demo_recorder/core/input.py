"""Synthetic input that behaves like a real mouse and keyboard.

Events are delivered with ``QApplication.sendEvent`` to the widget under a *virtual* cursor
(popups first), with an implicit grab while a button is held, hover enter/leave, and
click-to-focus - so buttons, sliders, menus and the napari canvas react as they would to a
person.  Nothing touches the real OS cursor, which makes it work under Xvfb and in CI.
"""

from __future__ import annotations

from qtpy.QtCore import QEvent, QPoint, QPointF, Qt
from qtpy.QtGui import QEnterEvent
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication, QWidget

from .qt_compat import MOUSE_BUTTONS, NO_MODIFIER, as_int, mouse_event, parse_key, wheel_event
from .selectors import popups


class VirtualInput:
    def __init__(self, window: QWidget, adapter=None):
        self.window = window
        self.adapter = adapter
        self.pos = QPoint(window.width() // 2, window.height() // 2)
        self.buttons = Qt.MouseButton.NoButton
        self.modifiers = NO_MODIFIER
        self._grabber: QWidget | None = None
        self._hover: QWidget | None = None

    # ------------------------------------------------------------------ geometry
    def global_pos(self, pos: QPoint | None = None) -> QPoint:
        return self.window.mapToGlobal(pos if pos is not None else self.pos)

    def target_at(self, pos: QPoint) -> tuple[QWidget, QPoint]:
        gpos = self.global_pos(pos)
        for popup in popups():
            local = popup.mapFromGlobal(gpos)
            if popup.rect().contains(local):
                child = popup.childAt(local) or popup
                return child, child.mapFromGlobal(gpos)
        active = QApplication.activePopupWidget()
        if active is not None:  # click outside an open popup goes to the popup (which closes it)
            return active, active.mapFromGlobal(gpos)
        child = self.window.childAt(pos) or self.window
        return child, child.mapFromGlobal(gpos)

    # ------------------------------------------------------------------ mouse
    def move(self, pos: QPoint) -> None:
        self.pos = QPoint(pos)
        gpos = self.global_pos()
        if self._grabber is not None:
            target = self._grabber
            local = target.mapFromGlobal(gpos)
        else:
            target, local = self.target_at(self.pos)
            self._update_hover(target, local, gpos)
            if not _tracks_mouse(target):
                return
        ev = mouse_event(QEvent.Type.MouseMove, local, gpos, Qt.MouseButton.NoButton, self.buttons, self.modifiers)
        QApplication.sendEvent(target, ev)

    def press(self, button: str = "left") -> QWidget:
        btn = MOUSE_BUTTONS[button]
        target, local = self.target_at(self.pos)
        gpos = self.global_pos()
        self._update_hover(target, local, gpos)
        _focus_on_click(target)
        self.buttons = Qt.MouseButton(as_int(self.buttons) | as_int(btn))
        self._grabber = target
        QApplication.sendEvent(target, mouse_event(QEvent.Type.MouseButtonPress, local, gpos, btn, self.buttons, self.modifiers))
        return target

    def release(self, button: str = "left") -> None:
        btn = MOUSE_BUTTONS[button]
        gpos = self.global_pos()
        target = self._grabber
        if target is None or not _alive(target):
            target, _ = self.target_at(self.pos)
        local = target.mapFromGlobal(gpos)
        self.buttons = Qt.MouseButton(as_int(self.buttons) & ~as_int(btn))
        if as_int(self.buttons) == 0:
            self._grabber = None
        QApplication.sendEvent(target, mouse_event(QEvent.Type.MouseButtonRelease, local, gpos, btn, self.buttons, self.modifiers))
        if self._grabber is None:
            new_target, new_local = self.target_at(self.pos)
            self._update_hover(new_target, new_local, gpos)

    def double_click_event(self, button: str = "left") -> None:
        """Send the MouseButtonDblClick that Qt generates for a second press."""
        btn = MOUSE_BUTTONS[button]
        target, local = self.target_at(self.pos)
        gpos = self.global_pos()
        self.buttons = Qt.MouseButton(as_int(self.buttons) | as_int(btn))
        self._grabber = target
        QApplication.sendEvent(target, mouse_event(QEvent.Type.MouseButtonDblClick, local, gpos, btn, self.buttons, self.modifiers))

    def wheel(self, steps: float) -> None:
        target, local = self.target_at(self.pos)
        ev = wheel_event(local, self.global_pos(), QPoint(0, round(120 * steps)), self.buttons, self.modifiers)
        QApplication.sendEvent(target, ev)

    def _update_hover(self, target: QWidget, local: QPoint, gpos: QPoint) -> None:
        if target is self._hover:
            return
        old = self._hover if self._hover is not None and _alive(self._hover) else None
        old_chain = _chain(old)
        new_chain = _chain(target)
        for w in old_chain:
            if w not in new_chain:
                w.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
                QApplication.sendEvent(w, QEvent(QEvent.Type.Leave))
                w.update()
        for w in reversed(new_chain):
            if w not in old_chain:
                w.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
                lp = QPointF(w.mapFromGlobal(gpos))
                QApplication.sendEvent(w, QEnterEvent(lp, lp, QPointF(gpos)))
                w.update()
        self._hover = target

    # ------------------------------------------------------------------ keyboard
    def key_target(self) -> QWidget:
        popup = QApplication.activePopupWidget()
        if popup is not None:
            return popup.focusWidget() or popup
        fw = QApplication.focusWidget()
        if fw is not None and (fw.window() is self.window or fw.window() in popups()):
            return fw
        if self.adapter is not None:
            tgt = self.adapter.default_key_target() if hasattr(self.adapter, "default_key_target") else None
            if tgt is not None:
                return tgt
        return self.window

    def key(self, keys: str) -> None:
        key, mods = parse_key(keys)
        QTest.keyClick(self.key_target(), Qt.Key(key), mods)

    def key_press(self, keys: str) -> None:
        key, mods = parse_key(keys)
        QTest.keyPress(self.key_target(), Qt.Key(key), mods)
        self.modifiers = mods

    def key_release(self, keys: str) -> None:
        key, mods = parse_key(keys)
        QTest.keyRelease(self.key_target(), Qt.Key(key), mods)
        self.modifiers = NO_MODIFIER

    def type_char(self, ch: str) -> None:
        QTest.keyClicks(self.key_target(), ch)


def _alive(w: QWidget) -> bool:
    try:
        w.objectName()
        return True
    except RuntimeError:
        return False


def _chain(w: QWidget | None) -> list[QWidget]:
    out = []
    while w is not None:
        out.append(w)
        if w.isWindow():
            break
        w = w.parentWidget()
    return out


def _tracks_mouse(w: QWidget) -> bool:
    while w is not None:
        if w.hasMouseTracking():
            return True
        if w.isWindow():
            return False
        w = w.parentWidget()
    return False


def _focus_on_click(w: QWidget) -> None:
    click_focus = as_int(Qt.FocusPolicy.ClickFocus)
    while w is not None:
        if as_int(w.focusPolicy()) & click_focus and w.isEnabled():
            w.setFocus(Qt.FocusReason.MouseFocusReason)
            return
        if w.isWindow():
            return
        w = w.parentWidget()
