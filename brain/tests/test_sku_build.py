from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from glimpse_brain.sku.build import build_index


def write_png(path: Path, color: tuple[int, int, int] = (10, 20, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(path, format="PNG")


class CountingEmbedder:
    """Deterministic per-call vector; records how many images it embedded."""

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, jpeg_bytes: bytes) -> np.ndarray:
        self.calls += 1
        v = np.array([float(len(jpeg_bytes)), float(self.calls)], dtype=np.float32)
        return v / np.linalg.norm(v)


def test_build_index_uses_filename_stem_as_id(tmp_path: Path) -> None:
    write_png(tmp_path / "example-a.png")
    write_png(tmp_path / "shipping.jpg")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")  # non-image
    embedder = CountingEmbedder()
    index = build_index(tmp_path, embedder)
    assert len(index) == 2  # txt skipped
    assert embedder.calls == 2
    hits = index.query(np.array([0.0, 1.0], dtype=np.float32), k=2)
    assert {sku for sku, _ in hits} == {"example-a", "shipping"}


def test_build_index_empty_dir(tmp_path: Path) -> None:
    index = build_index(tmp_path, CountingEmbedder())
    assert len(index) == 0
