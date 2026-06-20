from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from glimpse_brain.sku.embedder import SkuEmbedder


def jpeg_bytes(color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_preprocess_shape_and_dtype() -> None:
    # _preprocess is pure (no ONNX session needed) — call it on the class via
    # __new__ to avoid loading a model.
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    arr = embedder._preprocess(jpeg_bytes())
    assert arr.shape == (1, 3, 224, 224)
    assert arr.dtype == np.float32
    # CLIP normalization puts values well outside [0,1]; a flat color image must
    # not be all zeros after (x-mean)/std.
    assert not np.allclose(arr, 0.0)


def test_preprocess_handles_grayscale_and_rgba() -> None:
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    for mode in ("L", "RGBA"):
        buf = io.BytesIO()
        Image.new(mode, (64, 64)).save(buf, format="PNG")
        arr = embedder._preprocess(buf.getvalue())
        assert arr.shape == (1, 3, 224, 224)  # converted to 3-channel RGB


def test_preprocess_rejects_garbage_bytes() -> None:
    embedder = SkuEmbedder.__new__(SkuEmbedder)
    with pytest.raises(Exception):
        embedder._preprocess(b"not an image")
