"""Offline: build the SKU visual index from a directory of product images.
Run: python -m glimpse_brain.sku.build <image_dir> <out.npz> [--model PATH]
Filename stem = SKU id (= the OKF product doc id). Local compute, no API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol

import numpy as np

from glimpse_brain.sku.index import SkuIndex

_EXTS = {".jpg", ".jpeg", ".png"}


class _Embedder(Protocol):
    def embed(self, jpeg_bytes: bytes) -> np.ndarray: ...


def build_index(image_dir: Path, embedder: _Embedder) -> SkuIndex:
    paths = sorted(p for p in image_dir.glob("*") if p.suffix.lower() in _EXTS)
    vectors: list[np.ndarray] = []
    ids: list[str] = []
    for path in paths:
        try:
            vec = embedder.embed(path.read_bytes())
        except Exception as exc:  # one bad image must not abort the build
            print(f"[skip] {path.name}: {exc}", file=sys.stderr)
            continue
        vectors.append(vec)
        ids.append(path.stem)
    if not vectors:
        return SkuIndex(np.zeros((0, 0), dtype=np.float32), [])
    return SkuIndex(np.stack(vectors).astype(np.float32), ids)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m glimpse_brain.sku.build")
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("~/.glimpse/sku/cnclip_vitb16.img.onnx").expanduser(),
    )
    args = parser.parse_args(argv)
    if not args.image_dir.is_dir():
        print(f"image dir not found: {args.image_dir}", file=sys.stderr)
        return 2
    if not args.model.exists():
        print(
            f"model not found: {args.model}\n"
            "（请先将 Chinese-CLIP ViT-B/16 图像编码器导出为 ONNX 放到该路径）",
            file=sys.stderr,
        )
        return 2
    from glimpse_brain.sku.embedder import SkuEmbedder

    index = build_index(args.image_dir, SkuEmbedder(args.model))
    index.save(args.out)
    print(f"indexed {len(index)} SKUs → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
