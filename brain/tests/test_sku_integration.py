from __future__ import annotations

import os
from pathlib import Path

import pytest

from glimpse_brain.sku.build import build_index
from glimpse_brain.sku.embedder import SkuEmbedder

pytestmark = pytest.mark.integration

_MODEL = os.environ.get("SKU_MODEL_PATH")
_IMAGES = os.environ.get("SKU_IMAGE_DIR")
_QUERY = os.environ.get("SKU_QUERY_IMAGE")
_EXPECTED = os.environ.get("SKU_EXPECTED_ID")


@pytest.mark.skipif(
    not (_MODEL and _IMAGES and _QUERY and _EXPECTED),
    reason="set SKU_MODEL_PATH, SKU_IMAGE_DIR, SKU_QUERY_IMAGE, SKU_EXPECTED_ID to run",
)
def test_real_clip_matches_expected_sku() -> None:
    embedder = SkuEmbedder(Path(_MODEL))
    index = build_index(Path(_IMAGES), embedder)
    assert len(index) > 0
    vec = embedder.embed(Path(_QUERY).read_bytes())
    hits = index.query(vec, k=3)
    assert _EXPECTED in [sku for sku, _ in hits]
