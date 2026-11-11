"""GIF / MP4 encoding with a file-size budget."""

from __future__ import annotations

import io
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .capture import FrameStore

#: GitHub stops rendering GIFs inline somewhere above this size.
DEFAULT_MAX_SIZE = 5 * 1024 * 1024


@dataclass
class EncodeResult:
    path: Path
    size: int
    scale: float
    fps: float
    colors: int
    frames: int
    duration: float
    fits: bool
    attempts: list[tuple[float, float, int, int]] = field(default_factory=list)

    def summary(self) -> str:
        mb = self.size / 1024 / 1024
        txt = (
            f"{self.path} ({mb:.2f} MB, {self.frames} frames, {self.duration:.1f} s, "
            f"scale {self.scale:g}, {self.fps:g} fps, {self.colors} colors)"
        )
        return txt if self.fits else txt + "  -- WARNING: larger than max_size"


def _timeline(store: FrameStore, fps: float | None) -> list[tuple[int, float]]:
    """(frame index, duration) pairs, resampled to ``fps`` if it is lower than the store's."""
    durations = list(store._durations)
    if not durations:
        return []
    if fps is None or fps >= store.fps - 1e-6:
        return list(enumerate(durations))
    starts = np.concatenate([[0.0], np.cumsum(durations)[:-1]])
    total = float(sum(durations))
    out: list[tuple[int, float]] = []
    step = 1.0 / fps
    n_ticks = max(1, int(np.ceil(total / step - 1e-9)))
    for k in range(n_ticks):
        t = k * step
        idx = int(np.searchsorted(starts, t + 1e-9, side="right") - 1)
        dur = min(step, total - t)
        if out and out[-1][0] == idx:
            out[-1] = (idx, out[-1][1] + dur)
        else:
            out.append((idx, dur))
    return out


def _centiseconds(timeline: list[tuple[int, float]]) -> list[tuple[int, int]]:
    """Round durations to GIF centiseconds without accumulating drift; drop 0-length frames."""
    out: list[tuple[int, int]] = []
    t = 0.0
    prev_cs = 0
    for idx, dur in timeline:
        t += dur
        cs = round(t * 100)
        d = cs - prev_cs
        if d < 2:  # browsers clamp tiny delays to 100 ms; merge into the next frame instead
            continue
        out.append((idx, d))
        prev_cs = cs
    if not out and timeline:
        out.append((timeline[-1][0], max(2, round(t * 100))))
    return out


def _frame_image(store: FrameStore, idx: int, scale: float) -> Image.Image:
    import zlib

    arr = np.frombuffer(zlib.decompress(store._data[idx]), np.uint8).reshape(store.shape)
    img = Image.fromarray(arr, "RGB")
    if abs(scale - 1.0) > 1e-6:
        w, h = img.size
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
    return img


def _global_palette(images: list[Image.Image], colors: int) -> Image.Image:
    sample = images if len(images) <= 24 else [images[i] for i in np.linspace(0, len(images) - 1, 24).astype(int)]
    w = max(im.width for im in sample)
    h = sum(im.height for im in sample)
    sheet = Image.new("RGB", (w, h))
    y = 0
    for im in sample:
        sheet.paste(im, (0, y))
        y += im.height
    # shrink the sheet so palette generation stays fast
    if sheet.width * sheet.height > 4_000_000:
        f = (4_000_000 / (sheet.width * sheet.height)) ** 0.5
        sheet = sheet.resize((max(1, int(sheet.width * f)), max(1, int(sheet.height * f))), Image.Resampling.NEAREST)
    return sheet.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def encode_gif_bytes(
    store: FrameStore, *, scale: float = 1.0, fps: float | None = None, colors: int = 256, loop: bool = True
) -> tuple[bytes, int]:
    """Encode with a global palette and transparency optimisation.

    Pixels that did not change since the previous frame are written as a reserved transparent
    index, which compresses extremely well (the same trick ``gifsicle -O3`` uses).  This matters
    for UI recordings where only a cursor or a caret changes per frame.
    """
    timeline = _centiseconds(_timeline(store, fps))
    images = {idx: _frame_image(store, idx, scale) for idx in dict.fromkeys(i for i, _ in timeline)}
    n_colors = max(2, min(colors, 256) - 1)  # reserve one index for transparency
    palette_img = _global_palette(list(images.values()), n_colors)
    pal = palette_img.getpalette()[: 3 * n_colors]
    trans = len(pal) // 3
    pal = pal + [255, 0, 255] + [0, 0, 0] * (255 - trans)
    indexed = {
        idx: np.asarray(im.quantize(palette=palette_img, dither=Image.Dither.NONE), dtype=np.uint8)
        for idx, im in images.items()
    }
    frames: list[Image.Image] = []
    prev = None
    for idx, _cs in timeline:
        cur = indexed[idx]
        out = cur if prev is None else np.where(cur == prev, np.uint8(trans), cur)
        im = Image.fromarray(out, "P")
        im.putpalette(pal)
        frames.append(im)
        prev = cur
    durations = [cs * 10 for _, cs in timeline]
    buf = io.BytesIO()
    kwargs = {"loop": 0} if loop else {}
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations if len(durations) > 1 else durations[0],
        optimize=False,
        disposal=1,
        transparency=trans,
        **kwargs,
    )
    data = buf.getvalue()
    gifsicle = shutil.which("gifsicle")
    if gifsicle:
        try:
            res = subprocess.run([gifsicle, "-O3"], input=data, capture_output=True, check=True, timeout=600)
            if res.stdout and len(res.stdout) < len(data):
                data = res.stdout
        except (subprocess.SubprocessError, OSError):
            pass
    return data, len(frames)


def budget_attempts(scale: float, fps: float, colors: int) -> list[tuple[float, float, int]]:
    steps = [
        (scale, fps, colors),
        (scale, fps, min(colors, 128)),
        (scale, min(fps, 10), min(colors, 128)),
        (scale * 0.8, min(fps, 10), min(colors, 128)),
        (scale * 0.66, min(fps, 10), min(colors, 128)),
        (scale * 0.66, min(fps, 7.5), min(colors, 64)),
        (scale * 0.5, min(fps, 7.5), min(colors, 64)),
    ]
    return list(dict.fromkeys(steps))


def encode_gif(
    store: FrameStore,
    path: str | Path,
    *,
    scale: float = 1.0,
    fps: float | None = None,
    colors: int = 256,
    loop: bool = True,
    max_size: int | None = DEFAULT_MAX_SIZE,
    log=print,
) -> EncodeResult:
    path = Path(path)
    fps = fps or store.fps
    attempts = budget_attempts(scale, fps, colors) if max_size else [(scale, fps, colors)]
    best = None
    tried = []
    for s, f, c in attempts:
        data, n = encode_gif_bytes(store, scale=s, fps=f, colors=c, loop=loop)
        tried.append((s, f, c, len(data)))
        if best is None or len(data) < len(best[0]):
            best = (data, n, s, f, c)
        if max_size is None or len(data) <= max_size:
            best = (data, n, s, f, c)
            break
        log(f"  {len(data) / 1024 / 1024:.2f} MB > budget with scale {s:g}, {f:g} fps, {c} colors; shrinking...")
    data, n, s, f, c = best
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return EncodeResult(
        path=path,
        size=len(data),
        scale=s,
        fps=f,
        colors=c,
        frames=n,
        duration=store.duration,
        fits=max_size is None or len(data) <= max_size,
        attempts=tried,
    )


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def encode_mp4(store: FrameStore, path: str | Path, *, scale: float = 1.0, fps: float | None = None, log=print) -> EncodeResult:
    exe = ffmpeg_exe()
    if exe is None:
        raise RuntimeError("MP4 output needs ffmpeg: pip install 'napari-demo-recorder[mp4]'")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fps = fps or store.fps
    first = _frame_image(store, 0, scale)
    w, h = first.width + first.width % 2, first.height + first.height % 2
    cmd = [
        exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
        "-r", f"{fps:g}", "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
        "-movflags", "+faststart", str(path),
    ]  # fmt: skip
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = 0
    t_out = 0.0
    t_in = 0.0
    for idx, dur in enumerate(store._durations):
        t_in += dur
        img = None
        while t_out < t_in - 1e-9:
            if img is None:
                img = _frame_image(store, idx, scale)
                canvas = Image.new("RGB", (w, h))
                canvas.paste(img, (0, 0))
                raw = canvas.tobytes()
            proc.stdin.write(raw)
            n += 1
            t_out += 1.0 / fps
    proc.stdin.close()
    proc.wait()
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed with exit code {proc.returncode}")
    size = path.stat().st_size
    return EncodeResult(path, size, scale, fps, 0, n, store.duration, True)
