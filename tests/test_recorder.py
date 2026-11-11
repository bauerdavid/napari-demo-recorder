"""Record the example demo while the player performs it, and compare."""

from napari_demo_recorder import load_demo
from napari_demo_recorder.core.player import Player
from napari_demo_recorder.core.recorder import Recorder, steps_to_demo


def test_recorder_turns_events_into_semantic_steps(qapp, examples):
    spec = load_demo(examples / "qt_app.demo.yaml")
    p = Player(spec, mode="run", log=None)
    p.start()
    rec = Recorder(p.window, p.adapter, spontaneous_only=False, holds=False)
    rec.start()
    for step in spec.steps:
        p.run_step(step)
    qapp.processEvents()
    steps = rec.stop()
    got = [(s.name, s.params) for s in steps]
    names = [n for n, _ in got]

    assert ("set", {"widget": "Color", "parent": "Tools", "value": "Orange"}) in got
    assert ("set", {"widget": "Brush size", "parent": "Tools", "value": 18}) in got
    assert ("click", {"button": "Mirror strokes", "parent": "Tools"}) in got
    assert ("menu", {"path": ["File", "Export", "As PNG"]}) in got
    assert ("set", {"widget": "Title", "parent": "Tools", "value": "My sketch"}) in got
    assert names.count("drag") == 2

    # the recording is a valid demo that replays to the same result
    text = steps_to_demo(steps, app={"factory": "qt_app:make_window"}, window=p.window)
    p.adapter.close()
    out = examples.parent / "tests" / "_recorded.demo.yaml"
    try:
        out.write_text(text)
        replay = Player(load_demo(out), mode="run", log=None)
        replay.run(close=False)
        replay.spec.base_dir  # noqa: B018
        win = replay.window
        assert win.mirror_box.isChecked()
        assert win.exported == ["png"]
        assert win.title_edit.text() == "My sketch"
        assert len(win.sketch.strokes) == 2
        replay.adapter.close()
    finally:
        out.unlink(missing_ok=True)
