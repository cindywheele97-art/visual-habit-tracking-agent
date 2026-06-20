from __future__ import annotations

from pathlib import Path

import numpy as np

from glimpse_brain.sku.index import SkuIndex


def unit(*vals: float) -> np.ndarray:
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_query_returns_nearest_by_cosine_descending() -> None:
    idx = SkuIndex(
        np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]),
        ["east", "north", "ne"],
    )
    hits = idx.query(unit(1, 0.2), k=3)
    assert [sku for sku, _ in hits] == ["east", "ne", "north"]
    assert hits[0][1] > hits[1][1] > hits[2][1]  # scores descending


def test_query_respects_k() -> None:
    idx = SkuIndex(np.stack([unit(1, 0), unit(0, 1), unit(1, 1)]), ["a", "b", "c"])
    assert len(idx.query(unit(1, 0), k=2)) == 2


def test_empty_index_returns_empty() -> None:
    idx = SkuIndex(np.zeros((0, 0), dtype=np.float32), [])
    assert idx.query(unit(1, 0), k=5) == []
    assert len(idx) == 0


def test_save_load_round_trips(tmp_path: Path) -> None:
    idx = SkuIndex(np.stack([unit(1, 0), unit(0, 1)]), ["a", "b"])
    path = tmp_path / "index.npz"
    idx.save(path)
    loaded = SkuIndex.load(path)
    assert len(loaded) == 2
    hits = loaded.query(unit(1, 0), k=1)
    assert hits[0][0] == "a"
