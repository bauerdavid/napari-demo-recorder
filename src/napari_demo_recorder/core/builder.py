"""Generate demo YAML from Python.

    from napari_demo_recorder import Demo

    d = Demo(app="napari", size=(1280, 800), output="docs/demo.gif")
    d.setup.open_sample("napari", "cells3d")
    for tool in ["Paint", "Fill"]:
        d.click(button=tool, parent="layer_controls").hold(0.5)
    d.save("docs/demo.demo.yaml")

Every step type is available as a method (positional arguments map to the step's shorthand,
``class_`` / ``type_`` style names drop the trailing underscore).  The result is plain YAML, which
stays the canonical, editable format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .format import FORMAT_VERSION, DemoSpec, Step, dump_demo, parse_demo
from .steps import StepRegistry


class Section:
    def __init__(self, demo: Demo, name: str):
        self._demo = demo
        self._name = name
        self.steps: list[Step] = []

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        step_name = name.rstrip("_")
        registry = self._demo.registry
        if step_name not in registry:
            raise AttributeError(f"unknown step {step_name!r}; known: {', '.join(sorted(registry))}")
        st = registry[step_name]

        def add(*args: Any, **kwargs: Any) -> Section:
            params = {k.rstrip("_"): v for k, v in kwargs.items()}
            if args:
                sh = st.shorthand
                if sh is None:
                    raise TypeError(f"{step_name}() takes keyword arguments only")
                if isinstance(sh, list):
                    if len(args) > len(sh):
                        raise TypeError(f"{step_name}() takes at most {len(sh)} positional arguments")
                    params.update(dict(zip(sh, args)))
                elif len(args) == 1:
                    params[sh] = args[0]
                else:
                    raise TypeError(f"{step_name}() takes one positional argument ({sh})")
            params = {k: _plain(v) for k, v in params.items()}
            self.steps.append(Step(step_name, params))
            return self

        add.__name__ = step_name
        add.__doc__ = st.doc
        return add

    def __len__(self) -> int:
        return len(self.steps)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(self._demo.registry))


class Demo:
    def __init__(
        self,
        app: str | dict = "napari",
        *,
        title: str | None = None,
        size: tuple[int, int] | None = None,
        fps: float | None = None,
        output: str | dict | None = None,
        style: dict | None = None,
        speed: float | None = None,
        seed: int | None = None,
        window: dict | None = None,
    ):
        self.data: dict[str, Any] = {"demo": FORMAT_VERSION, "app": app}
        if title:
            self.data["title"] = title
        win = dict(window or {})
        if size:
            win["size"] = list(size)
        if win:
            self.data["window"] = win
        out = {"path": output} if isinstance(output, str) else dict(output or {})
        if fps:
            out["fps"] = fps
        if out:
            self.data["output"] = out
        if style:
            self.data["style"] = dict(style)
        if speed:
            self.data["speed"] = speed
        if seed is not None:
            self.data["seed"] = seed
        self.setup = Section(self, "setup")
        self.steps = Section(self, "steps")

    @property
    def registry(self) -> StepRegistry:
        from .adapter import registry_for

        return registry_for(DemoSpec(data=self.data).app_name)

    def __getattr__(self, name: str):
        if name.startswith("_") and not name.endswith("_") or name in ("data", "setup", "steps"):
            raise AttributeError(name)
        return getattr(self.steps, name)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(self.registry))

    # ------------------------------------------------------------------ conversion
    def to_dict(self) -> dict[str, Any]:
        d = dict(self.data)
        if self.setup.steps:
            d["setup"] = list(self.setup.steps)
        d["steps"] = list(self.steps.steps)
        return d

    def to_yaml(self, path: str | Path | None = None) -> str:
        text = dump_demo(self.to_dict(), self.registry)
        self.validate(text)
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(text, encoding="utf-8")
        return text

    save = to_yaml

    def validate(self, text: str | None = None) -> DemoSpec:
        from .format import load_raw

        text = text if text is not None else dump_demo(self.to_dict(), self.registry)
        return parse_demo(load_raw(text), self.registry)

    def spec(self, path: str | Path | None = None) -> DemoSpec:
        spec = self.validate()
        spec.path = Path(path).resolve() if path else None
        return spec

    def render(self, output: str | Path | None = None, **player_kwargs):
        """Render directly (needs a display, e.g. ``xvfb-run``)."""
        from .player import Player

        return Player(self.spec(), **player_kwargs).run(output)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Demo:
        from .format import load_demo

        spec = load_demo(path)
        demo = cls(app=spec.data.get("app", "qt"))
        demo.data = {k: v for k, v in spec.data.items() if k not in ("setup", "steps")}
        demo.setup.steps = list(spec.setup)
        demo.steps.steps = list(spec.steps)
        return demo


def _plain(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    try:
        import numpy as np

        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, np.ndarray):
            return v.tolist()
    except ImportError:
        pass
    return v
