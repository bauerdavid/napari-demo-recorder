"""Find widgets from the target keys of a step (``button``, ``widget``, ``parent``, ...)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import TYPE_CHECKING

from qtpy.QtCore import QPoint, QRect
from qtpy.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from .catalogue import Catalogue, norm, tooltip_keys, widget_matches_find
from .format import DemoError
from .qt_compat import as_int

if TYPE_CHECKING:
    from .adapter import Adapter

#: widgets whose objectName starts with this are part of the recorder itself and are ignored
INTERNAL_PREFIX = "demo_recorder_"

INPUT_TYPES = (QAbstractSlider, QAbstractSpinBox, QComboBox, QLineEdit, QCheckBox, QTextEdit, QPlainTextEdit)


@dataclass
class Scope:
    name: str | None
    roots: list[QWidget]
    excluded: list[QWidget] = field(default_factory=list)

    def widgets(self) -> list[QWidget]:
        out: list[QWidget] = []
        seen: set[int] = set()
        for root in self.roots:
            for w in [root, *root.findChildren(QWidget)]:
                if id(w) in seen or not is_visible(w) or self._is_excluded(w):
                    continue
                seen.add(id(w))
                out.append(w)
        return out

    def _is_excluded(self, w: QWidget) -> bool:
        return any(ex is w or ex.isAncestorOf(w) for ex in self.excluded)

    def contains(self, w: QWidget) -> bool:
        return any(r is w or r.isAncestorOf(w) for r in self.roots) and not self._is_excluded(w)


def is_visible(w: QWidget) -> bool:
    return (
        w.isVisible()
        and w.width() > 0
        and w.height() > 0
        and not w.objectName().startswith(INTERNAL_PREFIX)
        and not _has_internal_ancestor(w)
    )


def _has_internal_ancestor(w: QWidget) -> bool:
    p = w.parentWidget()
    while p is not None:
        if p.objectName().startswith(INTERNAL_PREFIX):
            return True
        p = p.parentWidget()
    return False


def popups() -> list[QWidget]:
    """Visible popup windows (menus, combo-box lists, ...), top-most first."""
    out = []
    active = QApplication.activePopupWidget()
    if active is not None:
        out.append(active)
    for w in QApplication.topLevelWidgets():
        if w in out or not w.isVisible() or w.objectName().startswith(INTERNAL_PREFIX):
            continue
        if isinstance(w, QMenu) or (as_int(w.windowType()) & 0x8):  # Qt.Popup bit
            out.append(w)
    return out


def dock_widgets(window: QWidget) -> list[QDockWidget]:
    return [d for d in window.findChildren(QDockWidget) if d.isVisible()]


# --------------------------------------------------------------------------- scope


def resolve_scope(window: QWidget, parent: str | None, adapter: Adapter | None) -> Scope:
    if parent is None:
        return Scope(None, [window, *popups()])
    if norm(parent) in ("window", "main window", "app"):
        return Scope(parent, [window, *popups()])
    if adapter is not None:
        scope = adapter.resolve_parent(parent, window)
        if scope is not None:
            return scope
    q = norm(parent)
    docks = [d for d in dock_widgets(window) if norm(d.windowTitle()) == q or norm(d.objectName()) == q]
    if docks:
        return Scope(parent, [d for d in docks])
    groups = [g for g in window.findChildren(QGroupBox) if is_visible(g) and norm(g.title()) == q]
    if groups:
        return Scope(parent, groups)
    named = [w for w in window.findChildren(QWidget) if is_visible(w) and w.objectName() and norm(w.objectName()) == q]
    if named:
        return Scope(parent, named)
    raise DemoError(f"no parent named {parent!r}. Available: {', '.join(available_parents(window, adapter))}")


def available_parents(window: QWidget, adapter: Adapter | None) -> list[str]:
    names = ["window"]
    if adapter is not None and adapter.catalogue is not None:
        names += list(adapter.catalogue.parents)
    names += [repr(d.windowTitle()) for d in dock_widgets(window) if d.windowTitle()]
    return names


def scope_from_parent_def(pdef, window: QWidget) -> Scope:
    """Build a scope from a catalogue :class:`ParentDef`."""
    if pdef.window:
        # the whole window except docks that are not listed (i.e. plugin docks)
        excluded = []
        if pdef.docks:
            keep = {norm(t) for t in pdef.docks}
            excluded = [d for d in window.findChildren(QDockWidget) if norm(d.windowTitle()) not in keep]
        return Scope(pdef.name, [window, *popups()], excluded)
    roots: list[QWidget] = []
    if pdef.docks:
        keep = {norm(t) for t in pdef.docks}
        roots += [d for d in window.findChildren(QDockWidget) if norm(d.windowTitle()) in keep]
    if pdef.classes or pdef.object_names:
        for w in window.findChildren(QWidget):
            names = {c.__name__ for c in type(w).__mro__}
            if (names & set(pdef.classes) or w.objectName() in pdef.object_names) and is_visible(w):
                roots.append(w)
    # drop roots nested in other roots
    roots = [r for r in roots if not any(o is not r and o.isAncestorOf(r) for o in roots)]
    return Scope(pdef.name, roots)


# --------------------------------------------------------------------------- matching


def magic_name(w: QWidget) -> str | None:
    mw = getattr(w, "_magic_widget", None)
    return getattr(mw, "name", None) if mw is not None else None


def widget_text(w: QWidget) -> str | None:
    if isinstance(w, QAbstractButton | QLabel):
        return w.text()
    if isinstance(w, QGroupBox):
        return w.title()
    return None


def label_target(label: QLabel) -> QWidget | None:
    """The input widget a label describes (buddy or layout neighbour)."""
    buddy = label.buddy()
    if buddy is not None:
        return buddy
    parent = label.parentWidget()
    while parent is not None:
        layout = parent.layout()
        if layout is not None:
            found = _layout_neighbour(layout, label)
            if found is not None:
                return found
        # magicgui-like containers: label inside a small wrapper widget
        if parent.layout() is not None and parent.layout().indexOf(label) >= 0:
            break
        parent = parent.parentWidget()
    return None


def _layout_neighbour(layout: QLayout, label: QWidget) -> QWidget | None:
    idx = layout.indexOf(label)
    if idx < 0:
        for i in range(layout.count()):
            sub = layout.itemAt(i).layout()
            if sub is not None:
                found = _layout_neighbour(sub, label)
                if found is not None:
                    return found
        return None
    if isinstance(layout, QFormLayout):
        row, _role = layout.getWidgetPosition(label)
        item = layout.itemAt(row, QFormLayout.ItemRole.FieldRole)
        return _item_widget(item)
    if isinstance(layout, QGridLayout):
        row, col, _rs, cs = layout.getItemPosition(idx)
        item = layout.itemAtPosition(row, col + cs)
        return _item_widget(item)
    if isinstance(layout, QBoxLayout):
        for j in range(idx + 1, layout.count()):
            w = _item_widget(layout.itemAt(j))
            if w is not None:
                return w
    return None


def _item_widget(item) -> QWidget | None:
    if item is None:
        return None
    if item.widget() is not None:
        return item.widget()
    sub = item.layout()
    if sub is not None:
        for i in range(sub.count()):
            w = _item_widget(sub.itemAt(i))
            if w is not None:
                return w
    return None


def input_widget(w: QWidget) -> QWidget:
    """For containers (magicgui labeled widgets, ...) return the most relevant input child."""
    if isinstance(w, INPUT_TYPES):
        return w
    for cls in INPUT_TYPES:
        kids = [k for k in w.findChildren(cls) if is_visible(k)]
        if kids:
            return kids[0]
    return w


def _remove_ancestors(matches: list[QWidget]) -> list[QWidget]:
    return [m for m in matches if not any(o is not m and m.isAncestorOf(o) for o in matches)]


def _dedupe(widgets: Iterable[QWidget]) -> list[QWidget]:
    out, seen = [], set()
    for w in widgets:
        if id(w) not in seen:
            seen.add(id(w))
            out.append(w)
    return out


def find_widgets(
    window: QWidget,
    params: dict,
    adapter: Adapter | None = None,
) -> list[QWidget]:
    """All widgets matching the target keys, best tier only."""
    query = params.get("button") or params.get("widget")
    want_button = "button" in params and "widget" not in params
    catalogue: Catalogue | None = adapter.catalogue if adapter is not None else None
    scope = resolve_scope(window, params.get("parent"), adapter)
    candidates = scope.widgets()

    def explicit(ws: list[QWidget]) -> list[QWidget]:
        out = ws
        if "class" in params:
            out = [w for w in out if params["class"] in {c.__name__ for c in type(w).__mro__}]
        if "object_name" in params:
            out = [w for w in out if w.objectName() == params["object_name"]]
        if "tooltip" in params:
            out = [w for w in out if norm(params["tooltip"]) in tooltip_keys(w.toolTip())]
        return out

    if not query:
        return _remove_ancestors(explicit(candidates)) if len(params.keys() & {"class", "object_name", "tooltip"}) else []

    q = norm(query)
    tiers: list[list[QWidget]] = []

    # 1. catalogue names / aliases
    if catalogue is not None:
        hits: list[QWidget] = []
        for el in catalogue.lookup(query):
            el_scope = scope
            if params.get("parent") is None and el.parent:
                pdef = catalogue.parents.get(el.parent)
                if pdef is not None:
                    el_scope = scope_from_parent_def(pdef, window)
            pool = el_scope.widgets() if el_scope is not scope else candidates
            for crit in el.find:
                found = [w for w in pool if widget_matches_find(w, crit)]
                if found:
                    hits += found
                    break
        tiers.append(hits)

    buttons = [w for w in candidates if isinstance(w, QAbstractButton)]
    pool = buttons if want_button else candidates

    # 2. visible text
    tiers.append([w for w in pool if norm(widget_text(w)) == q and not isinstance(w, QLabel)])
    # 3. magicgui parameter name / a label next to an input widget
    if not want_button:
        tiers.append([w for w in candidates if norm(magic_name(w)) == q])
        labelled = []
        for lab in candidates:
            if isinstance(lab, QLabel) and norm(lab.text()) == q:
                tgt = label_target(lab)
                if tgt is not None and scope.contains(tgt):
                    labelled.append(tgt)
        tiers.append(labelled)
    # 4. tooltip
    tiers.append([w for w in pool if q in tooltip_keys(w.toolTip())])
    # 5. accessible name / object name
    tiers.append([w for w in pool if q in (norm(w.accessibleName()), norm(w.objectName()))])
    # 6. plain labels (e.g. for highlight)
    if not want_button:
        tiers.append([w for w in candidates if isinstance(w, QLabel) and norm(w.text()) == q])

    for tier in tiers:
        found = _remove_ancestors(explicit(_dedupe(tier)))
        if found:
            return found
    return []


def find_widget(window: QWidget, params: dict, adapter: Adapter | None = None) -> QWidget:
    found = find_widgets(window, params, adapter)
    desc = describe_target(params)
    if not found:
        raise DemoError(f"no widget matches {desc}.{_suggest(window, params, adapter)}")
    index = params.get("index")
    if index is not None:
        if index >= len(found):
            raise DemoError(f"{desc}: index {index} out of range ({len(found)} matches)")
        return found[index]
    if len(found) > 1:
        what = ", ".join(_short(w) for w in found[:6])
        raise DemoError(f"{desc} is ambiguous ({len(found)} matches: {what}). Add 'parent' or 'index'.")
    return found[0]


def describe_target(params: dict) -> str:
    keys = ["button", "widget", "parent", "class", "object_name", "tooltip", "index"]
    return "{" + ", ".join(f"{k}: {params[k]!r}" for k in keys if k in params) + "}"


def _short(w: QWidget) -> str:
    label = widget_text(w) or magic_name(w) or (tooltip_keys(w.toolTip()) or [""])[0] or w.objectName()
    return f"{type(w).__name__}({label!r})" if label else type(w).__name__


def known_names(window: QWidget, params: dict, adapter: Adapter | None) -> list[str]:
    try:
        scope = resolve_scope(window, params.get("parent"), adapter)
    except DemoError:
        scope = resolve_scope(window, None, adapter)
    names: list[str] = []
    for w in scope.widgets():
        for n in (widget_text(w), magic_name(w), *(tooltip_keys(w.toolTip())[:1])):
            if n and n.strip():
                names.append(n.strip().split("\n")[0])
    if adapter is not None and adapter.catalogue is not None:
        for el in adapter.catalogue.elements:
            names += el.names()
    return list(dict.fromkeys(names))


def _suggest(window: QWidget, params: dict, adapter: Adapter | None) -> str:
    query = params.get("button") or params.get("widget")
    if not query:
        return ""
    names = known_names(window, params, adapter)
    by_norm = {norm(n): n for n in names}
    close = get_close_matches(norm(query), list(by_norm), n=4, cutoff=0.5)
    return f" Did you mean: {', '.join(repr(by_norm[c]) for c in close)}?" if close else ""


# --------------------------------------------------------------------------- geometry


def window_rect(window: QWidget, w: QWidget) -> QRect:
    """Rectangle of ``w`` in ``window`` coordinates (works for popups too)."""
    top_left = w.mapToGlobal(QPoint(0, 0)) - window.mapToGlobal(QPoint(0, 0))
    return QRect(top_left, w.size())


def window_center(window: QWidget, w: QWidget) -> QPoint:
    return window_rect(window, w).center()
