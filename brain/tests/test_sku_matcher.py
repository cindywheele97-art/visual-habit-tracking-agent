from __future__ import annotations

import numpy as np

from glimpse_brain.sku.index import SkuIndex
from glimpse_brain.sku.matcher import SkuMatcher


def unit(*vals: float) -> np.ndarray:
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    """Returns a fixed vector regardless of input bytes."""

    def __init__(self, vec: np.ndarray) -> None:
        self._vec = vec

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        return self._vec


def index() -> SkuIndex:
    return SkuIndex(np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]), ["east", "north", "ne"])


def test_match_returns_top_k_by_cosine() -> None:
    m = SkuMatcher(FakeEmbedder(unit(1, 0.1)), index(), top_k=2, min_score=0.0)
    hits = m.match(b"photo")
    assert [sku for sku, _ in hits] == ["east", "ne"]


def test_match_filters_below_min_score() -> None:
    # query == "east" direction → cosine(east, north) == 0 < 0.6 so north is filtered.
    m = SkuMatcher(FakeEmbedder(unit(1, 0)), index(), top_k=3, min_score=0.6)
    skus = [sku for sku, _ in m.match(b"photo")]
    assert "north" not in skus
    assert "east" in skus
