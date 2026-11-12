"""Loading, validating and writing ``*.demo.yaml`` files."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .steps import StepRegistry, StepType

FORMAT_VERSION = 1


class DemoError(Exception):
    """A problem with a demo file or with running one of its steps."""

    def __init__(self, message: str, *, location: str | None = None):
        self.location = location
        super().__init__(f"{location}: {message}" if location else message)


# --------------------------------------------------------------------------- top level schema

SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmM]?)i?[bB]?\s*$")

TOP_LEVEL_PROPS: dict[str, dict] = {
    "demo": {"const": FORMAT_VERSION, "description": "Format version."},
    "title": {"type": "string"},
    "app": {
        "description": "'napari', a 'module:factory' path, or {factory: ..., ...}.",
        "anyOf": [
            {"type": "string"},
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "factory": {"type": "string"}},
                "additionalProperties": True,
            },
        ],
    },
    "window": {
        "type": "object",
        "properties": {
            "size": {"type": "array", "items": {"type": "integer", "minimum": 100}, "minItems": 2, "maxItems": 2},
            "theme": {"type": "string"},
        },
        "additionalProperties": True,
    },
    "output": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "format": {"enum": ["gif", "mp4"]},
            "fps": {"type": "number", "exclusiveMinimum": 0, "maximum": 60},
            "scale": {"type": "number", "exclusiveMinimum": 0, "maximum": 4},
            "max_size": {"type": ["string", "number", "null"], "description": "e.g. '5MB'; null: no limit."},
            "colors": {"type": "integer", "minimum": 2, "maximum": 256},
            "loop": {"type": "boolean"},
            "end_hold": {"type": "number", "minimum": 0},
            "markdown": {"type": "boolean", "description": "Print a Markdown snippet for the output."},
            "readme": {
                "description": "Update this Markdown file between <!-- demo:NAME --> markers.",
                "anyOf": [
                    {"type": "string"},
                    {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "name": {"type": "string"}, "alt": {"type": "string"}},
                        "required": ["path"],
                    },
                ],
            },
            "url_base": {"type": "string", "description": "Base URL for Markdown links (default: GitHub raw URL)."},
        },
        "additionalProperties": False,
    },
    "style": {
        "type": "object",
        "properties": {
            "cursor": {"type": "boolean"},
            "cursor_scale": {"type": "number"},
            "accent": {"type": "string", "description": "Colour of highlights and click ripples."},
            "caption_position": {"enum": ["top", "bottom"]},
            "caption_size": {"type": "number"},
            "keys_position": {"enum": ["bottom-left", "bottom-right", "bottom", "top-left", "top-right"]},
            "palette": {
                "enum": ["accent", "warm", "cyan-magenta", "green", "white", "black-yellow"],
                "description": "Colours of arrows, step circles and boxes (accent: use 'accent').",
            },
        },
        "additionalProperties": False,
    },
    "interaction": {
        "enum": ["strict", "relaxed"],
        "description": (
            "strict (default): during 'steps' the app may only change through simulated input "
            "(clicks, drags, keys); direct changes need 'effect:' on the step. relaxed: direct "
            "changes are allowed. Either way they are marked in the GIF."
        ),
    },
    "speed": {"type": "number", "exclusiveMinimum": 0},
    "seed": {"type": "integer"},
    "setup": {"type": "array"},
    "steps": {"type": "array"},
}


def top_level_schema() -> dict:
    return {
        "type": "object",
        "properties": TOP_LEVEL_PROPS,
        "required": ["demo", "steps"],
        "additionalProperties": False,
    }


def full_schema(registry: StepRegistry) -> dict:
    """The complete JSON Schema (for editors / LLMs) for the given step registry."""
    defs = {f"step_{name}": st.json_schema() for name, st in sorted(registry.items())}
    step_ref = {"anyOf": [{"$ref": f"#/$defs/step_{name}"} for name in sorted(registry)]}
    props = dict(TOP_LEVEL_PROPS)
    props["setup"] = {"type": "array", "items": step_ref}
    props["steps"] = {"type": "array", "items": step_ref}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/bauerdavid/napari-demo-recorder/demo.schema.json",
        "title": "napari-demo-recorder demo",
        "type": "object",
        "properties": props,
        "required": ["demo", "steps"],
        "additionalProperties": False,
        "$defs": defs,
    }


# --------------------------------------------------------------------------- data model


@dataclass
class Step:
    name: str
    params: dict[str, Any]
    location: str = ""

    def to_yaml_obj(self, step_type: StepType | None = None) -> Any:
        params = {k: v for k, v in self.params.items()}
        if step_type is not None and step_type.shorthand and params:
            sh = step_type.shorthand
            if isinstance(sh, str) and set(params) == {sh} and not isinstance(params[sh], dict):
                return {self.name: params[sh]}
            if isinstance(sh, list) and set(params) <= set(sh):
                # use the list shorthand only if no optional key is skipped in the middle
                vals = [params[k] for k in sh if k in params]
                if [k for k in sh if k in params] == sh[: len(vals)]:
                    return {self.name: vals}
        return {self.name: params or None}


@dataclass
class DemoSpec:
    data: dict[str, Any]
    setup: list[Step] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    path: Path | None = None

    @property
    def app(self) -> dict[str, Any]:
        app = self.data.get("app", "qt")
        if isinstance(app, str):
            return {"factory": app} if ":" in app else {"name": app}
        return dict(app)

    @property
    def app_name(self) -> str:
        app = self.app
        return app.get("name") or ("qt" if "factory" in app else "napari")

    @property
    def strict(self) -> bool:
        return self.data.get("interaction", "strict") == "strict"

    @property
    def output(self) -> dict[str, Any]:
        return dict(self.data.get("output") or {})

    @property
    def window(self) -> dict[str, Any]:
        return dict(self.data.get("window") or {})

    @property
    def style(self) -> dict[str, Any]:
        return dict(self.data.get("style") or {})

    @property
    def base_dir(self) -> Path:
        return self.path.parent if self.path else Path.cwd()

    def output_path(self, override: str | Path | None = None) -> Path:
        if override:
            return Path(override)
        out = self.output.get("path")
        if out:
            p = Path(out)
            return p if p.is_absolute() else self.base_dir / p
        stem = self.path.name.split(".")[0] if self.path else "demo"
        ext = self.output.get("format", "gif")
        return self.base_dir / f"{stem}.{ext}"


def parse_size(value: str | float | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    m = SIZE_RE.match(value)
    if not m:
        raise DemoError(f"Cannot parse size {value!r} (use e.g. '5MB')")
    num, unit = float(m.group(1)), m.group(2).lower()
    return int(num * {"": 1, "k": 1024, "m": 1024 * 1024}[unit])


# --------------------------------------------------------------------------- normalizing steps


def normalize_step(raw: Any, registry: StepRegistry, location: str) -> Step:
    if isinstance(raw, str) and raw in registry:
        raw = {raw: None}
    if not isinstance(raw, dict) or len(raw) != 1:
        raise DemoError(
            "each step must be a mapping with exactly one step name, e.g. '- click: {button: Run}'",
            location=location,
        )
    (name, value), = raw.items()
    name = str(name)
    st = registry.get(name)
    if st is None:
        from difflib import get_close_matches

        close = get_close_matches(name, list(registry), n=3)
        hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
        raise DemoError(f"unknown step {name!r}.{hint}", location=location)
    params = _expand_shorthand(st, value, location)
    try:
        jsonschema.validate(params, st.params_schema())
    except jsonschema.ValidationError as err:
        where = "/".join(str(p) for p in err.absolute_path)
        raise DemoError(f"{name}: {err.message}" + (f" (at '{where}')" if where else ""), location=location) from None
    return Step(name=name, params=params, location=location)


def _expand_shorthand(st: StepType, value: Any, location: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return _plain(value)
    sh = st.shorthand
    if sh is None:
        raise DemoError(f"{st.name}: expected a mapping of parameters, got {value!r}", location=location)
    if isinstance(sh, list):
        if not isinstance(value, list):
            value = [value]
        if len(value) > len(sh):
            raise DemoError(f"{st.name}: expected at most {len(sh)} values ({', '.join(sh)})", location=location)
        return {k: _plain(v) for k, v in zip(sh, value)}
    return {sh: _plain(value)}


def _plain(obj: Any) -> Any:
    """ruamel CommentedMap / CommentedSeq -> plain dict / list."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_plain(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- load / dump


def _yaml(typ: str = "rt") -> YAML:
    y = YAML(typ=typ)
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 100
    y.preserve_quotes = True
    return y


def load_raw(source: str | Path | io.TextIOBase) -> dict:
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
        text = Path(source).read_text(encoding="utf-8")
    elif isinstance(source, str):
        text = source
    else:
        text = source.read()
    try:
        data = _yaml().load(text)
    except Exception as err:  # ruamel raises many error types
        raise DemoError(f"invalid YAML: {err}") from None
    if not isinstance(data, dict):
        raise DemoError("a demo file must be a YAML mapping with at least 'demo: 1' and 'steps:'")
    return data


def parse_demo(data: dict, registry: StepRegistry | None = None, path: Path | None = None) -> DemoSpec:
    from .adapter import registry_for

    data = _plain(data)
    loc = str(path) if path else "<demo>"
    try:
        jsonschema.validate(data, top_level_schema())
    except jsonschema.ValidationError as err:
        where = "/".join(str(p) for p in err.absolute_path) or "top level"
        raise DemoError(f"{err.message} (at '{where}')", location=loc) from None
    spec = DemoSpec(data=data, path=path)
    if registry is None:
        registry = registry_for(spec.app_name)
    spec.setup = [normalize_step(s, registry, f"{loc}: setup[{i}]") for i, s in enumerate(data.get("setup") or [])]
    spec.steps = [normalize_step(s, registry, f"{loc}: steps[{i}]") for i, s in enumerate(data.get("steps") or [])]
    if spec.strict:
        for step in spec.steps:
            if registry[step.name].direct and not step.params.get("effect"):
                raise DemoError(
                    f"'{step.name}' changes the app without visible input, which a viewer could "
                    "not reproduce. Move it to 'setup', use an input-based step"
                    f"{_input_alternative(step.name)}, or mark it with 'effect: <what happened>' "
                    "(shown in the GIF). 'interaction: relaxed' allows it for the whole demo.",
                    location=step.location,
                )
    if "output" in data:
        parse_size(data["output"].get("max_size"))
    return spec


_ALTERNATIVES = {
    "camera": " (zoom, pan, rotate)",
    "open_sample": " (menu: [File, Open Sample, ...])",
    "open_plugin": " (menu: [Plugins, ...])",
    "open_file": " (menu: [File, ...])",
}


def _input_alternative(name: str) -> str:
    return _ALTERNATIVES.get(name, "")


def load_demo(path: str | Path, registry: StepRegistry | None = None) -> DemoSpec:
    path = Path(path)
    return parse_demo(load_raw(path), registry, path=path.resolve())


def _flow(obj: Any, depth: int = 0) -> Any:
    """Make step parameters compact (flow style) while keeping lists of steps block style."""
    if isinstance(obj, dict):
        m = CommentedMap()
        for k, v in obj.items():
            m[k] = _flow(v, depth + 1)
        m.fa.set_flow_style()
        return m
    if isinstance(obj, list):
        s = CommentedSeq([_flow(v, depth + 1) for v in obj])
        s.fa.set_flow_style()
        return s
    return obj


def dump_demo(data: dict, registry: StepRegistry | None = None) -> str:
    """Serialise a demo dict (with plain ``setup``/``steps`` lists) to YAML text."""
    from .adapter import registry_for

    out = CommentedMap()
    for key in ["demo", "title", "app", "interaction", "window", "output", "style", "speed", "seed"]:
        if key in data and data[key] is not None:
            val = data[key]
            out[key] = _flow(val) if isinstance(val, dict | list) else val
    app_name = DemoSpec(data=data).app_name
    registry = registry or registry_for(app_name)
    for section in ("setup", "steps"):
        steps = data.get(section)
        if steps is None or (section == "setup" and not steps):
            continue
        seq = CommentedSeq()
        for s in steps:
            obj = s.to_yaml_obj(registry.get(s.name)) if isinstance(s, Step) else s
            (name, value), = obj.items()
            item = CommentedMap()
            item[name] = _flow(value) if isinstance(value, dict | list) else value
            seq.append(item)
        out[section] = seq
    buf = io.StringIO()
    _yaml().dump(out, buf)
    return buf.getvalue()
