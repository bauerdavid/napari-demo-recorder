"""Arrows, numbered step circles and boxes: anchored callouts."""

import numpy as np
import pytest

from napari_demo_recorder import DemoError
from napari_demo_recorder.core.capture import compose_frame
from napari_demo_recorder.core.format import Step, load_raw, parse_demo
from napari_demo_recorder.core.player import Player
from napari_demo_recorder.core.selectors import window_rect

QT = "demo: 1\napp: {factory: 'qt_app:make_window'}\n"


@pytest.fixture
def player(qapp, examples):
    spec = parse_demo(load_raw(QT + "steps: []\n"), path=examples / "x.yaml")
    p = Player(spec, log=None)
    p.start()
    p.capturing = True
    yield p
    p.adapter.close()


def _changed(p, region, t):
    """Fraction of pixels in ``region`` (x0, y0, x1, y1) that the overlays change at time t."""
    x0, y0, x1, y1 = region
    with_ann = compose_frame(p.window, p.overlay, t).astype(int)[y0:y1, x0:x1]
    saved, p.overlay.annotations = p.overlay.annotations, []
    without = compose_frame(p.window, p.overlay, t).astype(int)[y0:y1, x0:x1]
    p.overlay.annotations = saved
    return (np.abs(with_ann - without).sum(-1) > 60).mean()


def test_annotation_steps_are_allowed_in_strict_mode():
    spec = parse_demo(load_raw(QT + "steps:\n  - arrow: Clear\n  - step_circle: {widget: Color}\n  - box: {widget: sketch, fill: true}\n  - clear_annotations: {}\n"))
    assert [s.name for s in spec.steps] == ["arrow", "step_circle", "box", "clear_annotations"]


def test_arrow_points_at_the_widget(player):
    p = player
    p.overlay.cursor = None
    p.run_step(Step("arrow", {"button": "Clear", "from": "left", "label": "reset", "keep": True}))
    btn = window_rect(p.window, p.window.clear_button)
    cy = btn.center().y()
    # the shaft runs left of the button, ending just before its left edge
    assert _changed(p, (btn.left() - 60, cy - 4, btn.left() - 4, cy + 4), p.t) > 0.3
    assert _changed(p, (btn.left() + 10, cy - 4, btn.right() - 10, cy + 4), p.t) < 0.05  # button not covered


def test_annotations_follow_the_widget(player):
    p = player
    p.run_step(Step("box", {"widget": "Clear", "keep": True}))
    rect_before = p.overlay.annotations[0].anchor()
    p.window.resize(p.window.width() + 200, p.window.height())
    p.settle(0.1)
    rect_after = p.overlay.annotations[0].anchor()
    assert rect_after.left() > rect_before.left() + 100  # the dock moved with the window edge


def test_step_numbers_auto_increment(player):
    p = player
    for params in ({"widget": "Color"}, {"widget": "Title"}, {"widget": "Clear", "number": 7}, {"widget": "Color"}):
        p.run_step(Step("step_circle", {**params, "hold": False}))
    assert [a.number for a in p.overlay.annotations] == [1, 2, 7, 8]


def test_hold_keep_and_clear(player):
    p = player
    t0 = p.t
    p.run_step(Step("arrow", {"button": "Clear", "hold": False}))
    assert p.t == t0  # hold: false -> no frames, the next step runs while it shows
    assert p.overlay.annotations[-1].t1 == pytest.approx(t0 + 1.8)
    p.run_step(Step("box", {"widget": "sketch", "keep": True, "duration": 0.5}))
    assert p.t == pytest.approx(t0 + 0.5, abs=p.dt)
    assert p.overlay.annotations[-1].t1 is None
    p.run_step(Step("clear_annotations", {}))
    assert all(a.t1 is not None and a.t1 <= p.t for a in p.overlay.annotations)
    for _ in range(10):
        p.frame()
    p.overlay.prune(p.t)
    assert p.overlay.annotations == []


def test_hidden_target_is_not_drawn(player):
    p = player
    p.run_step(Step("box", {"button": "Clear", "keep": True, "hold": False}))
    p.window.clear_button.hide()
    assert p.overlay.annotations[0].anchor() is None
    compose_frame(p.window, p.overlay, p.t)  # must not raise


def test_annotation_needs_a_target(player):
    with pytest.raises(DemoError, match="needs a target"):
        player.run_step(Step("arrow", {"label": "nothing"}))
