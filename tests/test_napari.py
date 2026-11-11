"""napari adapter: catalogue, coordinates, napari steps, the showcase demo and the widget."""

import numpy as np
import pytest

pytestmark = pytest.mark.napari

napari = pytest.importorskip("napari")

from napari_demo_recorder import DemoError, load_demo  # noqa: E402
from napari_demo_recorder.core.format import load_raw, parse_demo  # noqa: E402
from napari_demo_recorder.core.player import Player  # noqa: E402
from napari_demo_recorder.core.selectors import find_widget, find_widgets  # noqa: E402
from napari_demo_recorder.napari_app.adapter import NapariAdapter  # noqa: E402


def make_player(steps="[]", setup=None, mode="run", path=None):
    text = f"demo: 1\napp: napari\nwindow: {{size: [1200, 800]}}\nsteps: {steps}\n"
    if setup:
        text += f"setup: {setup}\n"
    return Player(parse_demo(load_raw(text), path=path), mode=mode, log=None)


@pytest.fixture
def player(qapp):
    p = make_player()
    p.start()
    yield p
    p.adapter.close()


@pytest.fixture
def viewer(player):
    return player.adapter.viewer


# layer type that must be selected for a layer_controls element to be visible
NEEDS_LAYER = {
    "paint": "labels", "erase": "labels", "fill": "labels", "pick": "labels",
    "labels_polygon": "labels", "shuffle_colors": "labels", "pan_zoom": "labels", "transform": "labels",
    "add_points": "points", "select": "points",
    "direct_select": "shapes", "add_rectangle": "shapes", "add_ellipse": "shapes", "add_line": "shapes",
    "add_polyline": "shapes", "add_path": "shapes", "add_polygon": "shapes",
    "insert_vertex": "shapes", "remove_vertex": "shapes", "polygon_lasso": "shapes",
    "delete_selected": "shapes", "move_to_front": "shapes", "move_to_back": "shapes",
}  # fmt: skip


def _elements():
    from napari_demo_recorder.core.catalogue import Catalogue
    from napari_demo_recorder.napari_app.adapter import CATALOGUE_FILE

    return [e.id for e in Catalogue.from_file(CATALOGUE_FILE).elements]


def _select(viewer, kind):
    layers = {
        "labels": lambda: viewer.add_labels(np.zeros((32, 32), int)),
        "points": lambda: viewer.add_points(np.zeros((0, 2))),
        "shapes": lambda: viewer.add_shapes(),
    }
    layer = layers[kind]()
    viewer.layers.selection.active = layer


@pytest.mark.parametrize("element", _elements())
def test_catalogue_element_resolves(player, viewer, element, qapp):
    """Every catalogue entry must resolve on the napari version under test."""
    el = player.adapter.catalogue.element(element)
    if element in NEEDS_LAYER:
        _select(viewer, NEEDS_LAYER[element])
    player.settle(0.3)
    if element in ("add_polyline", "labels_polygon", "polygon_lasso", "transform") and not find_widgets(player.window, {"button": el.id}, player.adapter):
        pytest.skip(f"{element} does not exist in napari {napari.__version__}")
    for name in [el.id, *el.aliases]:
        found = find_widgets(player.window, {"button": name}, player.adapter)
        assert len(found) == 1, f"{name!r} -> {found}"
    # also inside the 'napari' parent and the element's own parent
    assert len(find_widgets(player.window, {"button": el.id, "parent": "napari"}, player.adapter)) == 1
    assert len(find_widgets(player.window, {"button": el.id, "parent": el.parent}, player.adapter)) == 1


def test_create_points_out_of_the_box(qapp):
    p = make_player(steps="[{click: {button: Create Points, parent: napari}}]")
    p.run(close=False)
    assert [type(lyr).__name__ for lyr in p.adapter.viewer.layers] == ["Points"]
    p.adapter.close()


def test_plugin_docks_are_not_part_of_napari_parent(player, viewer):
    from magicgui.widgets import PushButton

    viewer.window.add_dock_widget(PushButton(text="Delete"), name="My plugin")
    player.settle(0.1)
    # catalogue alias "Delete" resolves to napari's delete button, never to the plugin's button
    w = find_widget(player.window, {"button": "Delete", "parent": "napari"}, player.adapter)
    assert w.property("mode") == "delete_button"
    w2 = find_widget(player.window, {"button": "Delete", "parent": "My plugin"}, player.adapter)
    assert w2.text() == "Delete"


def test_layer_controls_by_label(player, viewer):
    viewer.add_labels(np.zeros((32, 32), int))
    player.settle(0.1)
    w = find_widget(player.window, {"widget": "brush size", "parent": "layer_controls"}, player.adapter)
    assert "Slider" in type(w).__name__


def test_canvas_round_trip(player, viewer):
    viewer.add_image(np.zeros((5, 100, 200)), scale=(1, 2, 0.5), name="img")
    player.settle(0.2)
    a: NapariAdapter = player.adapter
    world = a.data_to_world(viewer.layers["img"], [40, 120])
    pt = a.world_to_canvas(world)
    back = a.canvas_to_world(pt)
    assert np.allclose(back[-2:], world[-2:], atol=1e-6)
    assert np.allclose(world[-2:], [80, 60])


def test_describe_point_is_data_coords(player, viewer):
    viewer.add_image(np.zeros((100, 200)), scale=(2, 0.5), name="img")
    player.settle(0.2)
    a = player.adapter
    pt = a.world_to_canvas(a.data_to_world(viewer.layers["img"], [30, 50]))
    d = a.describe_point(a.canvas_widget(), pt)
    assert d["layer"] == "img"
    assert np.allclose(d["canvas"], [30, 50], atol=0.2)


def test_dims_and_layer_list_steps(qapp):
    p = make_player(
        steps="[{dims: [0, 7]}, {select_layer: a}, {toggle_visibility: b}]",
        setup="[{python: 'viewer.add_image(np.zeros((10, 32, 32)), name=\"a\"); viewer.add_image(np.zeros((10, 32, 32)), name=\"b\")'}]",
    )
    p.run(close=False)
    v = p.adapter.viewer
    assert v.dims.current_step[0] == 7
    assert v.layers.selection.active.name == "a"
    assert v.layers["b"].visible is False
    p.adapter.close()


def test_unknown_layer_error(qapp):
    p = make_player(steps="[{select_layer: nope}]")
    with pytest.raises(DemoError, match="no layer 'nope'"):
        p.run()


def test_showcase_demo(qapp, examples):
    spec = load_demo(examples / "showcase.demo.yaml")
    p = Player(spec, mode="run", log=None)
    p.run(close=False)
    v = p.adapter.viewer
    assert len(v.layers["blobs found"].data) > 5
    assert v.layers["blobs found"].visible is False
    labels = v.layers["Labels"]
    painted = np.count_nonzero(labels.data)
    assert painted > 100  # first stroke stays
    assert labels.brush_size == 12
    assert v.camera.zoom == pytest.approx(4.5)
    assert v.scale_bar.visible
    p.adapter.close()


def test_recorder_in_napari(qapp):
    from napari_demo_recorder.core.recorder import Recorder

    p = make_player(setup="[{python: 'viewer.add_image(np.random.rand(64, 64), name=\"img\")'}]")
    p.run(close=False)
    rec = Recorder(p.window, p.adapter, spontaneous_only=False, holds=False)
    rec.start()
    steps = parse_demo(
        load_raw(
            "demo: 1\napp: napari\nsteps:\n"
            "  - click: {button: Create Labels, parent: napari}\n"
            "  - drag: {canvas: [[10, 10], [30, 40]], layer: img}\n"
            "  - select_layer: img\n"
        )
    ).steps
    for s in steps:
        p.run_step(s)
    qapp.processEvents()
    got = rec.stop()
    names = [(s.name, s.params) for s in got]
    assert ("click", {"button": "Create Labels", "parent": "layer_buttons"}) in names
    drags = [s for s in got if s.name == "drag"]
    assert drags  # the new labels layer is selected (its name differs between napari versions)
    assert np.allclose(drags[0].params["canvas"][0], [10, 10], atol=1.0)
    # the drag pans the camera (pan/zoom mode); points still use the transform at press time
    assert np.allclose(drags[0].params["canvas"][-1], [30, 40], atol=1.0)
    assert ("select_layer", {"layer": "img"}) in names
    p.adapter.close()


def test_widget(player, viewer, examples):
    from napari_demo_recorder.napari_app._widget import DemoRecorderWidget

    w = DemoRecorderWidget(viewer)
    viewer.window.add_dock_widget(w, name="Demo recorder")
    w.path_edit.setText(str(examples / "showcase.demo.yaml"))
    w._load()
    assert w.steps_list.count() == 1 + len(load_demo(examples / "showcase.demo.yaml").steps)
    w.record_btn.setChecked(True)
    assert w.recorder is not None and w.recorder.active
    rec = w.recorder
    # the widget's own clicks are never recorded
    assert not rec._relevant(w.play_btn)
    rec.stop()
    w.recorder = None
    w.record_btn.setChecked(False)
