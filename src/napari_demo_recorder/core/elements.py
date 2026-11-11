"""``list-elements``: what can be targeted in a running app (handy for writing demos)."""

from __future__ import annotations

from qtpy.QtWidgets import QAbstractButton, QDockWidget, QLabel, QWidget

from .catalogue import tooltip_keys, widget_matches_find
from .selectors import (
    INPUT_TYPES,
    label_target,
    magic_name,
    resolve_scope,
    scope_from_parent_def,
    widget_text,
)


def list_elements(window: QWidget, adapter=None, parent: str | None = None) -> list[str]:
    lines: list[str] = []
    cat = adapter.catalogue if adapter is not None else None
    if cat is not None and parent is None:
        lines.append("# parents (use as 'parent:')")
        for name, pdef in cat.parents.items():
            n = len(scope_from_parent_def(pdef, window).roots)
            lines.append(f"  {name}{'' if n else '   (not visible now)'}")
    docks = [d.windowTitle() for d in window.findChildren(QDockWidget) if d.isVisible() and d.windowTitle()]
    if docks and parent is None:
        lines.append("# docks (also usable as 'parent:')")
        lines += [f"  {t!r}" for t in docks]
    if cat is not None:
        lines.append("# catalogue elements (name: aliases)")
        for el in cat.elements:
            if parent is not None and el.parent != parent:
                continue
            pdef = cat.parents.get(el.parent) if el.parent else None
            scope = scope_from_parent_def(pdef, window) if pdef else resolve_scope(window, None, adapter)
            pool = scope.widgets()
            found = any(any(widget_matches_find(w, c) for w in pool) for c in el.find)
            mark = "" if found else "   (not visible now)"
            lines.append(f"  {el.id} [{el.parent}]: {', '.join(el.aliases)}{mark}")
    scope = resolve_scope(window, parent, adapter)
    lines.append("# buttons (text / tooltip)")
    for w in scope.widgets():
        if isinstance(w, QAbstractButton):
            label = widget_text(w) or (tooltip_keys(w.toolTip()) or [""])[0]
            if label.strip():
                lines.append(f"  button: {label.strip()!r}")
    lines.append("# value widgets (label / magicgui name)")
    for w in scope.widgets():
        if isinstance(w, QLabel) and w.text().strip():
            tgt = label_target(w)
            if tgt is not None and (isinstance(tgt, INPUT_TYPES) or tgt.findChildren(INPUT_TYPES)):
                lines.append(f"  widget: {w.text().strip().rstrip(':')!r}  ({type(tgt).__name__})")
        name = magic_name(w)
        if name and isinstance(w, INPUT_TYPES):
            lines.append(f"  widget: {name!r}  (magicgui {type(w).__name__})")
    return list(dict.fromkeys(lines))
