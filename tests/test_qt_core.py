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
    spec = parse_demo(load_raw("demo: 1\napp: {factory: 'qt_app:make_window'}\nsteps:\n  - python: 'x = 1'\n"), path=examples / "x.yaml")
    with pytest.raises(DemoError, match="disabled"):
        Player(spec, mode="run", allow_python=False, log=None).run()


def test_python_step_namespace(qapp, examples):
    spec = parse_demo(
        load_raw("demo: 1\napp: {factory: 'qt_app:make_window'}\nsteps:\n  - python: 'root.title_edit.setText(\"from python\")'\n"),
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
