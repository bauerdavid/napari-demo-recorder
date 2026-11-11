"""A small dummy plugin widget ("Blob finder") used for the showcase demo.

It is not registered as a napari plugin; the demo loads it with a ``python:`` setup step::

    setup:
      - python: showcase:setup
"""

from __future__ import annotations

import napari
import numpy as np
from magicgui import magic_factory
from scipy import ndimage as ndi


def make_blobs(shape=(256, 256), n=28, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    img = np.zeros(shape, float)
    for _ in range(n):
        cy, cx = rng.uniform(15, shape[0] - 15), rng.uniform(15, shape[1] - 15)
        r = rng.uniform(4, 9)
        img += rng.uniform(0.5, 1.0) * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * r * r))
    img += rng.normal(0, 0.04, shape)
    return np.clip(img, 0, None).astype(np.float32)


@magic_factory(
    call_button="Detect",
    sigma={"widget_type": "FloatSlider", "min": 1.0, "max": 10.0, "step": 0.5},
    threshold={"widget_type": "FloatSlider", "min": 0.0, "max": 1.0, "step": 0.05},
    size={"widget_type": "SpinBox", "min": 2, "max": 40, "label": "point size"},
)
def blob_finder(
    image: napari.layers.Image,
    sigma: float = 2.0,
    threshold: float = 0.6,
    size: int = 8,
) -> napari.types.LayerDataTuple:
    """Find bright blobs with a Laplacian-of-Gaussian filter."""
    data = np.asarray(image.data, float)
    response = -ndi.gaussian_laplace(data, sigma) * sigma**2
    peaks = (response == ndi.maximum_filter(response, size=7)) & (response > threshold * response.max())
    coords = np.argwhere(peaks)
    return coords, {"name": "blobs found", "size": size, "face_color": "#ff9f1c", "border_color": "white"}, "points"


def setup(viewer):
    """Add the sample image and the widget (used by the demo's setup)."""
    viewer.add_image(make_blobs(), name="blobs", colormap="magma")
    viewer.window.add_dock_widget(blob_finder(), name="Blob finder", area="right")
