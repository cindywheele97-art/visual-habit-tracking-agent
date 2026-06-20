"""CLIP image-encoder embeddings via onnxruntime (no torch at inference). The
single home of Chinese-CLIP's preprocessing (224px bicubic + CLIP mean/std).
The ONNX session is created in __init__ so _preprocess is unit-testable without
a model (instantiate via SkuEmbedder.__new__)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

# OpenAI/CLIP normalization constants (Chinese-CLIP uses the same).
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class SkuEmbedder:
    def __init__(self, model_path: Path) -> None:
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def _preprocess(self, jpeg_bytes: bytes) -> np.ndarray:
        img = (
            Image.open(io.BytesIO(jpeg_bytes))
            .convert("RGB")
            .resize((224, 224), Image.Resampling.BICUBIC)
        )
        arr = np.asarray(img, dtype=np.float32) / 255.0  # (224,224,3)
        arr = (arr - _MEAN) / _STD
        arr = arr.transpose(2, 0, 1)[None, :, :, :]  # (1,3,224,224)
        return np.ascontiguousarray(arr, dtype=np.float32)

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        x = self._preprocess(jpeg_bytes)
        out = self._session.run(None, {self._input_name: x})[0]  # (1, D)
        vec = np.asarray(out[0], dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec
