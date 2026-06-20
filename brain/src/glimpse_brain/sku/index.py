"""Cosine nearest-neighbor over L2-normalized SKU vectors. Brute-force numpy —
ample at a few-thousand-SKU scale. Persists to a single .npz."""

from __future__ import annotations

from pathlib import Path

import numpy as np


class SkuIndex:
    def __init__(self, vectors: np.ndarray, sku_ids: list[str]) -> None:
        self._vectors = vectors  # (N, D) float32, each row L2-normalized
        self._sku_ids = sku_ids

    def __len__(self) -> int:
        return len(self._sku_ids)

    def query(self, vec: np.ndarray, k: int) -> list[tuple[str, float]]:
        if not self._sku_ids:
            return []
        sims = self._vectors @ vec  # cosine, both sides normalized
        order = np.argsort(-sims)[:k]
        return [(self._sku_ids[i], float(sims[i])) for i in order]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, vectors=self._vectors, ids=np.array(self._sku_ids))

    @classmethod
    def load(cls, path: Path) -> SkuIndex:
        data = np.load(path)
        return cls(data["vectors"], [str(x) for x in data["ids"]])
