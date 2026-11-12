import jsonschema
import pytest

from napari_demo_recorder import Demo, DemoError
from napari_demo_recorder.core.adapter import registry_for
from napari_demo_recorder.core.format import (
    dump_demo,
    full_schema,
    load_demo,
    load_raw,
    parse_demo,
    parse_size,
)


def parse(text, app="napari"):
    return parse_demo(load_raw(text))


def test_shorthands_expand():
    spec = parse(
        """
demo: 1
app: napari
setup:
  - open_sample: [napari, cells3d]
steps:
  - click: Detect
  - key: Ctrl+Z
  - hold: 1.5
  - caption: Hello
  - menu: [File, Open Sample]
  - set: {widget: sigma, value: 3}
"""
    )
    assert spec.setup[0].params == {"plugin": "napari", "sample": "cells3d"}
    names = [(s.name, s.params) for s in spec.steps]
    assert names[0] == ("click", {"button": "Detect"})
    assert names[1] == ("key", {"keys": "Ctrl+Z"})
    assert names[2] == ("hold", {"seconds": 1.5})
    assert names[3] == ("caption", {"text": "Hello"})
    assert names[4] == ("menu", {"path": ["File", "Open Sample"]})


def test_unknown_step_suggests():
    with pytest.raises(DemoError, match="Did you mean 'click'"):
        parse("demo: 1\napp: napari\nsteps:\n  - clik: Run\n")


def test_bad_param_reports_location():
    with pytest.raises(DemoError, match=r"steps\[0\].*hold"):
        parse("demo: 1\napp: napari\nsteps:\n  - hold: {seconds: -1}\n")
    with pytest.raises(DemoError, match="Additional properties"):
        parse("demo: 1\napp: napari\nsteps:\n  - click: {buton: Run}\n")


def test_top_level_validation():
    with pytest.raises(DemoError, match="'steps' is a required property"):
        parse("demo: 1\n")
    with pytest.raises(DemoError):
        parse("demo: 2\nsteps: []\n")


def test_napari_steps_only_for_napari():
    with pytest.raises(DemoError, match="unknown step 'open_sample'"):
        parse("demo: 1\napp: {factory: 'x:y'}\nsteps:\n  - open_sample: [napari, cells3d]\n")


def test_parse_size():
    assert parse_size("5MB") == 5 * 1024 * 1024
    assert parse_size("500 kB") == 500 * 1024
    assert parse_size(1000) == 1000
    assert parse_size(None) is None
    with pytest.raises(DemoError):
        parse_size("lots")


def test_dump_roundtrip_uses_shorthands():
    spec = parse("demo: 1\napp: napari\nsteps:\n  - click: {button: Run}\n  - set: {widget: a, value: 1}\n")
    text = dump_demo({"demo": 1, "app": "napari", "steps": spec.steps})
    assert "- click: Run" in text
    assert "- set: {widget: a, value: 1}" in text
    again = parse(text)
    assert [(s.name, s.params) for s in again.steps] == [(s.name, s.params) for s in spec.steps]


def test_builder_generates_valid_yaml():
    d = Demo(app="napari", size=(1000, 700), output="docs/x.gif", fps=10)
    d.setup.open_sample("napari", "cells3d")
    for tool in ["Paint", "Fill"]:
        d.click(button=tool, parent="layer_controls").hold(0.5)
    d.drag(canvas=[(10, 10), (20, 30)], layer="cells")
    d.set(widget="opacity", value=0.5)
    d.python("viewer.layers[0].visible = False", effect="layer hidden")
    text = d.to_yaml()
    spec = parse(text)
    assert len(spec.steps) == 7
    assert spec.steps[0].params == {"button": "Paint", "parent": "layer_controls"}
    assert spec.steps[4].params["canvas"] == [[10, 10], [20, 30]]
    assert spec.data["output"] == {"path": "docs/x.gif", "fps": 10}


def test_builder_rejects_unknown_step():
    with pytest.raises(AttributeError, match="unknown step"):
        Demo().klick(button="x")


def test_builder_class_keyword():
    d = Demo(app={"factory": "m:f"})
    d.click(button="OK", class_="QPushButton")
    assert d.steps.steps[0].params == {"button": "OK", "class": "QPushButton"}


def test_builder_from_yaml(examples, tmp_path):
    d = Demo.from_yaml(examples / "showcase.demo.yaml")
    d.hold(2)
    out = tmp_path / "x.demo.yaml"
    d.save(out)
    spec = load_demo(out)
    assert spec.steps[-1].name == "hold"
    assert spec.setup[0].name == "python"


@pytest.mark.parametrize("name", ["showcase.demo.yaml", "qt_app.demo.yaml"])
def test_examples_validate_against_json_schema(examples, name):
    spec = load_demo(examples / name)
    raw = load_raw(examples / name)
    from napari_demo_recorder.core.format import _plain

    jsonschema.validate(_plain(raw), full_schema(registry_for(spec.app_name)))


def test_schema_rejects_bad_step():
    schema = full_schema(registry_for("napari"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"demo": 1, "steps": [{"click": {"nope": 1}}]}, schema)
    jsonschema.validate({"demo": 1, "steps": [{"click": "Run"}, {"key": "Esc"}]}, schema)


STRICT_BASE = "demo: 1\napp: napari\n"


def test_strict_is_default_and_rejects_direct_steps():
    with pytest.raises(DemoError, match=r"steps\[0\].*'camera' changes the app.*zoom, pan, rotate"):
        parse(STRICT_BASE + "steps:\n  - camera: {zoom: 2}\n")
    with pytest.raises(DemoError, match="'python' changes the app"):
        parse(STRICT_BASE + "steps:\n  - python: 'x = 1'\n")
    with pytest.raises(DemoError, match="menu: \\[File, Open Sample"):
        parse(STRICT_BASE + "steps:\n  - open_sample: [napari, cells3d]\n")


def test_direct_steps_allowed_in_setup_marked_or_relaxed():
    assert parse(STRICT_BASE + "setup:\n  - camera: {zoom: 2}\n  - python: 'x = 1'\nsteps: []\n").setup
    spec = parse(STRICT_BASE + "steps:\n  - camera: {zoom: 2, effect: zoomed in}\n  - python: {code: 'x = 1', effect: true}\n")
    assert spec.steps[0].params["effect"] == "zoomed in"
    assert parse(STRICT_BASE + "interaction: relaxed\nsteps:\n  - camera: {zoom: 2}\n").steps
    with pytest.raises(DemoError):
        parse(STRICT_BASE + "interaction: loose\nsteps: []\n")


def test_input_steps_are_not_direct():
    reg = registry_for("napari")
    direct = sorted(n for n, st in reg.items() if st.direct)
    assert direct == ["camera", "open_file", "open_plugin", "open_sample", "python"]
