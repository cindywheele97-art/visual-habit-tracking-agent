"""Facade: embed a photo, query the index, apply top_k/min_score. The agent's
per-turn SKU dependency (built once at startup)."""

from __future__ import annotations

from typing import Protocol

import numpy as np

from glimpse_brain.sku.index import SkuIndex


class _Embedder(Protocol):
    def embed(self, jpeg_bytes: bytes) -> np.ndarray: ...


class SkuMatcher:
    def __init__(
        self, embedder: _Embedder, index: SkuIndex, top_k: int = 5, min_score: float = 0.0
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._top_k = top_k
        self._min_score = min_score

    def match(self, jpeg_bytes: bytes) -> list[tuple[str, float]]:
        vec = self._embedder.embed(jpeg_bytes)
        hits = self._index.query(vec, self._top_k)
        return [(sku, score) for sku, score in hits if score >= self._min_score]
