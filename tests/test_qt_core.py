"""Core behaviour against a plain Qt app (no napari)."""

import numpy as np
import pytest
from PIL import Image
from qtpy.QtCore import QPoint
from qtpy.QtWidgets import QComboBox, QDockWidget, QPushButton, QSlider

from napari_demo_recorder import DemoError, load_demo
from napari_demo_recorder.core.capture import compose_frame
from napari_demo_recorder.core.format import load_raw, parse_demo
from napari_demo_recorder.core.player import Player
from napari_demo_recorder.core.selectors import find_widget, find_widgets


@pytest.fixture
def player(qapp, examples):
    spec = load_demo(examples / "qt_app.demo.yaml")
    p = Player(spec, mode="run", log=None)
    p.start()
    yield p
    p.adapter.close()


def test_selectors(player):
    w = player.window
    assert isinstance(find_widget(w, {"widget": "Brush size"}), QSlider)
    assert isinstance(find_widget(w, {"widget": "color"}), QComboBox)
    assert isinstance(find_widget(w, {"button": "Clear", "parent": "Tools"}), QPushButton)
    assert isinstance(find_widget(w, {"widget": "tools"}), QDockWidget)  # objectName
    assert find_widget(w, {"widget": "sketch"}) is w.sketch


def test_selector_errors_are_helpful(player):
    with pytest.raises(DemoError, match="Did you mean: 'Clear'"):
        find_widget(player.window, {"button": "Clea"})
    with pytest.raises(DemoError, match="no parent named 'Nope'.*'Tools'"):
        find_widget(player.window, {"button": "Clear", "parent": "Nope"})


def test_index_disambiguates(player):
    found = find_widgets(player.window, {"class": "QLineEdit"})
    assert len(found) >= 1
    assert find_widget(player.window, {"class": "QLineEdit", "index": 0}) is found[0]


def test_demo_changes_app_state(player):
    for step in player.spec.steps:
        player.run_step(step)
    win = player.window
    strokes = win.sketch.strokes
    assert len(strokes) == 2
    assert strokes[0][0] == "Black" and strokes[1][0] == "Orange"
    assert strokes[1][1] == 18  # brush size set by dragging the slider
    assert len(strokes[0][2]) > 3  # a real path, not just endpoints
    assert win.mirror_box.isChecked()
    assert win.exported == ["png"]  # File > Export > As PNG
    assert win.title_edit.text() == "My sketch"


def test_overlays_are_composited(player):
    player.overlay.cursor = QPoint(200, 200)
    player.overlay.caption = "Hello"
    frame = compose_frame(player.window, player.overlay, 1.0)
    # cursor tip area is dark; the sketch background is light
    assert frame[205, 202].sum() < 150
    assert frame[150, 150].sum() > 600
    h = frame.shape[0]
    assert frame[h - 40, frame.shape[1] // 2].sum() < 200  # caption box


def test_render_writes_gif(qapp, examples, tmp_path):
    text = (examples / "qt_app.demo.yaml").read_text()
    spec = parse_demo(load_raw(text), path=examples / "qt_app.demo.yaml")
    spec.steps = spec.steps[:3]
    spec.data["output"] = {"fps": 8, "scale": 0.5}
    result = Player(spec, log=None).run(tmp_path / "out.gif")
    assert result.fits
    im = Image.open(result.path)
    assert im.n_frames > 10
    assert im.size == (450, 280)


def test_python_steps_can_be_disabled(qapp, examples):
    spec = parse_demo(load_raw("demo: 1\napp: {factory: 'qt_app:make_window'}\nsetup:\n  - python: 'x = 1'\nsteps: []\n"), path=examples / "x.yaml")
    with pytest.raises(DemoError, match="disabled"):
        Player(spec, mode="run", allow_python=False, log=None).run()


def test_python_step_namespace(qapp, examples):
    spec = parse_demo(
        load_raw("demo: 1\napp: {factory: 'qt_app:make_window'}\nsetup:\n  - python: 'root.title_edit.setText(\"from python\")'\nsteps: []\n"),
        path=examples / "x.yaml",
    )
    p = Player(spec, mode="run", log=None)
    p.run(close=False)
    assert p.window.title_edit.text() == "from python"
    p.adapter.close()


def test_frame_store_dedupes_static_frames(qapp, examples, tmp_path):
    spec = parse_demo(load_raw("demo: 1\napp: {factory: 'qt_app:make_window'}\nsteps:\n  - cursor: false\n  - hold: 2\n"), path=examples / "x.yaml")
    p = Player(spec, log=None)
    p.run(tmp_path / "x.gif", close=False)
    assert len(p.store) <= 3  # 2 s of nothing happening is one frame with a long duration
    assert p.store.duration == pytest.approx(2 + 1.2 + 2 / 15, abs=0.2)
    p.adapter.close()
    assert np.asarray(p.store.last()).ndim == 3


QT = "demo: 1\napp: {factory: 'qt_app:make_window'}\n"


def _qt_spec(examples, text):
    return parse_demo(load_raw(QT + text), path=examples / "x.yaml")


def test_marked_effect_is_drawn_and_held(qapp, examples, tmp_path):
    spec = _qt_spec(examples, "steps:\n  - python: {code: 'root.title_edit.setText(\"x\")', effect: title set by code}\n")
    p = Player(spec, log=None)
    p.start()
    p.capturing = True
    t0 = p.t
    p.run_step(spec.steps[0])
    assert p.t - t0 >= 1.2 - 1e-6  # an instant change still stays on screen long enough to read
    badge = [e for e in p.overlay.effects if e.text == "title set by code"]
    assert badge and badge[0].t1 is not None
    with_marker = compose_frame(p.window, p.overlay, badge[0].t0 + 0.5).astype(int)
    p.overlay.effects.clear()
    without = compose_frame(p.window, p.overlay, badge[0].t0 + 0.5).astype(int)
    corner = np.abs(with_marker[15:55, 15:200] - without[15:55, 15:200]).sum(-1)
    assert (corner > 60).mean() > 0.3  # the marker pill covers the top-left corner
    p.adapter.close()


def test_relaxed_marks_direct_steps_with_default_label(qapp, examples):
    spec = _qt_spec(examples, "interaction: relaxed\nsteps:\n  - python: 'x = 1'\n")
    p = Player(spec, mode="run", log=None)
    p.run(close=False)
    assert [e.text for e in p.overlay.effects] == ["code ran"]
    p.adapter.close()


def test_failed_interaction_is_an_error_in_strict_mode(qapp, examples):
    # the field only accepts digits: typing letters cannot produce the value
    setup = "setup:\n  - python: 'from qtpy.QtGui import QIntValidator; root.title_edit.setValidator(QIntValidator())'\n"
    spec = _qt_spec(examples, setup + "steps:\n  - set: {widget: Title, value: hello}\n")
    with pytest.raises(DemoError, match=r"steps\[0\].*typing 'hello'.*strict mode"):
        Player(spec, mode="run", log=None).run()
    # marked: allowed, and visibly marked
    spec = _qt_spec(examples, setup + "steps:\n  - set: {widget: Title, value: hello, effect: title forced}\n")
    p = Player(spec, mode="run", log=None)
    p.run(close=False)
    assert p.window.title_edit.text() == "hello"
    assert [e.text for e in p.overlay.effects] == ["title forced"]
    p.adapter.close()


def test_slider_reaches_value_by_dragging(player):
    from napari_demo_recorder.core.format import Step

    player.run_step(Step("set", {"widget": "Brush size", "value": 27}))
    assert player.window.size_slider.value() == 27
    assert not player.overlay.effects  # no direct change was needed
