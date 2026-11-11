"""UI catalogues: stable names and aliases for an application's own widgets.

A catalogue is a YAML file shipped by an adapter (e.g. ``napari_ui.yaml``)::

    parents:
      layer_buttons: {class: [QtLayerButtons]}
      napari: {window: true, docks: [layer controls, layer list]}
    elements:
      - id: new_points_layer
        parent: layer_buttons
        aliases: [Create Points, New points layer]
        find:
          - {property: {mode: new_points}}      # tried first
          - {tooltip: New points layer}         # fallback for other versions

Each ``find`` entry is tried in order until one matches visible widgets inside the parent.
Supported keys of a ``find`` entry (all given keys must match):
``property`` (Qt dynamic properties; values may be lists of alternatives), ``class``,
``object_name``, ``tooltip`` (normalized first line, exact), ``tooltip_startswith``, ``text``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_PAREN_SUFFIX = re.compile(r"\s*[\(\[][^\(\)\[\]]*[\)\]]\s*$")


def norm(text: str | None) -> str:
    """Normalize a label for matching: case, mnemonics, whitespace, trailing shortcut hints."""
    if not text:
        return ""
    t = str(text).replace("&&", "\0").replace("&", "").replace("\0", "&")
    t = t.replace("\xa0", " ").replace("_", " ").strip()
    prev = None
    while prev != t:
        prev = t
        t = _PAREN_SUFFIX.sub("", t).strip()
        t = t.rstrip(".:…").strip()
    return " ".join(t.lower().split())


def tooltip_keys(tooltip: str | None) -> list[str]:
    """Normalized forms of a tooltip: first line and first sentence."""
    if not tooltip:
        return []
    # strip rich text
    plain = re.sub(r"<[^>]+>", "", tooltip)
    first_line = plain.strip().split("\n")[0]
    keys = [norm(first_line)]
    first_sentence = re.split(r"(?<=[.!?])\s", first_line, maxsplit=1)[0]
    if norm(first_sentence) not in keys:
        keys.append(norm(first_sentence))
    return [k for k in keys if k]


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list | tuple) else [v]


@dataclass
class ParentDef:
    name: str
    classes: list[str] = field(default_factory=list)
    object_names: list[str] = field(default_factory=list)
    docks: list[str] = field(default_factory=list)
    window: bool = False
    aliases: list[str] = field(default_factory=list)


@dataclass
class Element:
    id: str
    parent: str | None
    aliases: list[str]
    find: list[dict[str, Any]]
    description: str = ""

    def names(self) -> list[str]:
        return [self.id, *self.aliases]

    def matches(self, query: str) -> bool:
        q = norm(query)
        return any(norm(n) == q for n in self.names())


@dataclass
class Catalogue:
    parents: dict[str, ParentDef] = field(default_factory=dict)
    elements: list[Element] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> Catalogue:
        data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> Catalogue:
        parents = {}
        for name, p in (data.get("parents") or {}).items():
            p = p or {}
            parents[name] = ParentDef(
                name=name,
                classes=_as_list(p.get("class")),
                object_names=_as_list(p.get("object_name")),
                docks=_as_list(p.get("docks") or p.get("dock")),
                window=bool(p.get("window", False)),
                aliases=_as_list(p.get("aliases")),
            )
        elements = [
            Element(
                id=e["id"],
                parent=e.get("parent"),
                aliases=_as_list(e.get("aliases")),
                find=_as_list(e.get("find")),
                description=e.get("description", ""),
            )
            for e in data.get("elements") or []
        ]
        return cls(parents=parents, elements=elements)

    def merged(self, other: Catalogue | None) -> Catalogue:
        if other is None:
            return self
        return Catalogue({**self.parents, **other.parents}, [*self.elements, *other.elements])

    def parent(self, name: str) -> ParentDef | None:
        q = norm(name)
        for p in self.parents.values():
            if norm(p.name) == q or any(norm(a) == q for a in p.aliases):
                return p
        return None

    def lookup(self, query: str) -> list[Element]:
        return [e for e in self.elements if e.matches(query)]

    def element(self, id_: str) -> Element | None:
        return next((e for e in self.elements if e.id == id_), None)


def widget_matches_find(widget, crit: dict[str, Any]) -> bool:
    """Does ``widget`` satisfy one ``find`` entry of a catalogue element?"""
    for key, expected in crit.items():
        if key == "property":
            for prop, values in expected.items():
                val = widget.property(prop)
                if val is None or str(val) not in [str(v) for v in _as_list(values)]:
                    return False
        elif key == "class":
            names = {c.__name__ for c in type(widget).__mro__}
            if not names & set(_as_list(expected)):
                return False
        elif key == "object_name":
            if widget.objectName() not in _as_list(expected):
                return False
        elif key == "tooltip":
            keys = tooltip_keys(widget.toolTip())
            if not any(norm(v) in keys for v in _as_list(expected)):
                return False
        elif key == "tooltip_startswith":
            tip = norm(widget.toolTip())
            if not any(tip.startswith(norm(v)) for v in _as_list(expected)):
                return False
        elif key == "text":
            text = widget.text() if hasattr(widget, "text") else None
            if norm(text) not in [norm(v) for v in _as_list(expected)]:
                return False
        else:
            raise ValueError(f"unknown catalogue find key {key!r}")
    return True
