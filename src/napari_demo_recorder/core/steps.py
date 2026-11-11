"""Step registry.

Every step type (``click``, ``key``, ``caption``, ...) is declared once with a JSON-schema
description of its parameters, an optional *shorthand* and a ``run`` generator.  The YAML
schema, the validation, the Python builder and the player are all derived from this registry,
so they cannot drift apart.

A ``run`` function receives the player and the normalized parameter dict and is a generator:
every ``yield`` advances the virtual clock by one frame (see :mod:`.player`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

RunFn = Callable[[Any, dict], Iterator[None]]

NUMBER = {"type": "number"}
POINT = {"type": "array", "items": NUMBER, "minItems": 2}
PATH = {"type": "array", "items": POINT, "minItems": 1}

#: Keys that identify a widget (or a position) - shared by every step that targets something.
TARGET_PROPS: dict[str, dict] = {
    "button": {"type": "string", "description": "Button text, tooltip, catalogue name or alias."},
    "widget": {
        "type": "string",
        "description": "Any widget: visible text, label next to it, magicgui name, tooltip, catalogue name.",
    },
    "parent": {
        "type": "string",
        "description": "Where to look: a dock title or an app area (e.g. 'napari', 'layer_controls').",
    },
    "index": {"type": "integer", "description": "Pick the n-th match (0-based) when several widgets match."},
    "object_name": {"type": "string"},
    "tooltip": {"type": "string"},
    "class": {"type": "string", "description": "Qt class name of the widget, e.g. QPushButton."},
    "canvas": {
        "description": "Point or path on the app's canvas (napari: data coordinates by default).",
        "anyOf": [POINT, PATH],
    },
    "coords": {
        "type": "string",
        "description": "Coordinate system of 'canvas'/'path': data, world, canvas (pixels), fraction.",
    },
    "layer": {"type": "string", "description": "napari: layer whose data coordinates are used."},
    "at": {"description": "Window pixel position [x, y] (or a path).", "anyOf": [POINT, PATH]},
    "path": {"description": "Points relative to the target widget (see 'coords').", "anyOf": [POINT, PATH]},
    "offset": {"description": "Pixel offset [dx, dy] from the target's centre.", **POINT},
}

#: Options every step accepts.
COMMON_PROPS: dict[str, dict] = {
    "duration": {"type": "number", "minimum": 0, "description": "Override the step's duration (s)."},
    "speed": {"type": "number", "exclusiveMinimum": 0, "description": "Speed multiplier for this step."},
    "pause": {"type": "number", "minimum": 0, "description": "Extra hold after the step (s)."},
    "capture": {"type": "boolean", "description": "false: run the step without recording frames."},
}


@dataclass
class StepType:
    name: str
    run: RunFn
    params: dict[str, dict] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    shorthand: str | list[str] | None = None
    doc: str = ""
    targets: bool = False
    #: False -> extra keys allowed (python steps pass kwargs through)
    strict: bool = True

    def properties(self) -> dict[str, dict]:
        props = dict(COMMON_PROPS)
        if self.targets:
            props.update(TARGET_PROPS)
        props.update(self.params)
        return props

    def params_schema(self) -> dict:
        return {
            "type": "object",
            "properties": self.properties(),
            "required": list(self.required),
            "additionalProperties": not self.strict,
        }

    def shorthand_schema(self) -> dict | None:
        if self.shorthand is None:
            return None
        if isinstance(self.shorthand, list):
            items = [self.properties()[k] for k in self.shorthand]
            return {"type": "array", "prefixItems": items, "minItems": len(self.required), "maxItems": len(items)}
        return self.properties()[self.shorthand]

    def json_schema(self) -> dict:
        """Schema of the step as it appears in a YAML list: ``{name: params-or-shorthand}``."""
        value = self.params_schema()
        short = self.shorthand_schema()
        if short is not None:
            value = {"anyOf": [value, short]}
        elif not self.required:
            value = {"anyOf": [value, {"type": "null"}]}
        return {
            "type": "object",
            "description": self.doc,
            "properties": {self.name: value},
            "required": [self.name],
            "additionalProperties": False,
        }


class StepRegistry(dict[str, StepType]):
    def step(
        self,
        name: str,
        *,
        params: dict[str, dict] | None = None,
        required: list[str] | None = None,
        shorthand: str | list[str] | None = None,
        targets: bool = False,
        strict: bool = True,
    ) -> Callable[[RunFn], RunFn]:
        def deco(fn: RunFn) -> RunFn:
            self[name] = StepType(
                name=name,
                run=fn,
                params=params or {},
                required=required or [],
                shorthand=shorthand,
                doc=(fn.__doc__ or "").strip().split("\n\n")[0].replace("\n", " "),
                targets=targets,
                strict=strict,
            )
            return fn

        return deco

    def merged(self, other: dict[str, StepType]) -> StepRegistry:
        reg = StepRegistry(self)
        reg.update(other)
        return reg


#: Steps available for every app.  Filled by :mod:`.builtin_steps`.
CORE_STEPS = StepRegistry()
