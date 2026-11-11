import io

import numpy as np
import pytest
from PIL import Image

from napari_demo_recorder.core.capture import FrameStore
from napari_demo_recorder.core.encode import (
    _centiseconds,
    _timeline,
    encode_gif,
    encode_gif_bytes,
    encode_mp4,
    ffmpeg_exe,
)


def make_store(n=30, size=(120, 160), fps=10):
    store = FrameStore(fps)
    rng = np.random.default_rng(0)
    gray = (rng.random(size) * 255).astype(np.uint8)
    bg = np.repeat(gray[..., None], 3, axis=-1)
    for i in range(n):
        f = bg.copy()
        f[20:30, 5 + i * 4 : 15 + i * 4] = [255, 0, 0]  # a moving red box
        store.append(f)
    return store


def decode(data):
    im = Image.open(io.BytesIO(data))
    frames = []
    for i in range(im.n_frames):
        im.seek(i)
        frames.append((np.asarray(im.convert("RGB")).astype(int), im.info.get("duration")))
    return frames


def test_store_merges_identical_frames():
    store = FrameStore(10)
    a = np.zeros((4, 4, 3), np.uint8)
    store.append(a)
    store.append(a.copy())
    store.append(a + 1)
    assert len(store) == 2
    assert store.duration == pytest.approx(0.3)


def test_gif_decodes_to_source_frames():
    store = make_store()
    data, n = encode_gif_bytes(store)
    frames = decode(data)
    assert len(frames) == n == 30
    src = [f.astype(int) for f, _ in store.frames()]
    for (got, _), want in zip(frames, src):
        # the moving box must be exactly where it is in the source
        def red(a):
            return (a[..., 0] > 200) & (a[..., 1] < 60) & (a[..., 2] < 60)

        assert np.array_equal(red(got), red(want))
        assert np.abs(got - want).mean() < 12  # palette error on the noise background


def test_transparency_optimisation_makes_static_content_cheap():
    store = make_store(n=60)
    data, _ = encode_gif_bytes(store)
    single = io.BytesIO()
    Image.fromarray(next(store.frames())[0]).save(single, format="GIF")
    # 60 frames of a mostly static image cost far less than 60 full frames
    assert len(data) < 6 * len(single.getvalue())


def test_durations_do_not_drift():
    store = FrameStore(15)
    for i in range(45):
        store.append(np.full((2, 2, 3), i, np.uint8))
    cs = _centiseconds(_timeline(store, None))
    assert sum(c for _, c in cs) == 300  # 45 frames at 15 fps = 3.00 s exactly
    assert all(c >= 2 for _, c in cs)


def test_resample_to_lower_fps_keeps_total_time():
    store = make_store(n=30, fps=15)
    tl = _timeline(store, 7.5)
    assert sum(d for _, d in tl) == pytest.approx(2.0)
    assert len(tl) == 15


def test_budget_loop_shrinks(tmp_path):
    store = make_store(n=40, size=(240, 320))
    full, _ = encode_gif_bytes(store)
    res = encode_gif(store, tmp_path / "x.gif", max_size=len(full) // 2, log=lambda *_: None)
    assert res.fits
    assert res.size <= len(full) // 2
    assert (res.scale, res.fps, res.colors) != (1.0, 10, 256)
    assert (tmp_path / "x.gif").stat().st_size == res.size


def test_no_budget(tmp_path):
    res = encode_gif(make_store(n=5), tmp_path / "y.gif", max_size=None, log=lambda *_: None)
    assert res.fits and len(res.attempts) == 1


@pytest.mark.skipif(ffmpeg_exe() is None, reason="ffmpeg not available")
def test_mp4(tmp_path):
    res = encode_mp4(make_store(n=20), tmp_path / "x.mp4", log=lambda *_: None)
    assert res.path.stat().st_size > 1000
    assert res.frames == 20
