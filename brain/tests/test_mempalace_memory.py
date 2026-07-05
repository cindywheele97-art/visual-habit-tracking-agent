"""Opt-in integration test for MemPalaceMemory against a real temp palace.
Slow (downloads the embedding model) -> excluded from the default pytest run.
Run explicitly: ./.venv/bin/python -m pytest tests/test_mempalace_memory.py -m integration -v"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

@pytest.mark.integration
async def test_write_then_recall_roundtrips_scoped_to_wing(tmp_path) -> None:
    from glimpse_brain.mempalace_memory import MemPalaceMemory

    mem = MemPalaceMemory(palace_path=tmp_path / "palace", embedding_model="minilm")
    await mem.write("测试客户", "曾因 SKU-A 破损退货", "fact")
    hits = await mem.recall("测试客户", "破损退货", k=5)
    assert any("破损" in h.text for h in hits)
    # scoped: a different wing sees nothing
    assert await mem.recall("其他客户", "破损退货", k=5) == []


def test_recall_error_result_logs_and_returns_empty(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    # WHY: mempalace errors must not masquerade as "no memories" without a trace.
    from glimpse_brain.mempalace_memory import MemPalaceMemory

    mem = MemPalaceMemory(palace_path=tmp_path / "palace", embedding_model="minilm")
    with patch(
        "mempalace.searcher.search_memories",
        return_value={"error": "index dimension mismatch"},
    ):
        with caplog.at_level(logging.WARNING, logger="glimpse.memory"):
            hits = mem._recall_sync("客户", "query", k=5)
    assert hits == []
    assert "mempalace recall failed" in caplog.text


def test_wing_key_distinguishes_similar_display_names() -> None:
    # WHY: _safe_wing collapsed "王先生" and " 王先生." to the same key — cross-customer pollution.
    from glimpse_brain.mempalace_memory import _wing

    assert _wing("王先生") != _wing(" 王先生.")


def test_wing_key_is_stable() -> None:
    from glimpse_brain.mempalace_memory import _wing

    assert _wing("王先生") == _wing("王先生")


def test_write_metadata_carries_display_name(tmp_path) -> None:
    # WHY: hashed wing keys are opaque — the original OCR display name must land in metadata for ops.
    from unittest.mock import MagicMock, patch

    from glimpse_brain.mempalace_memory import MemPalaceMemory, _wing

    mem = MemPalaceMemory(palace_path=tmp_path / "palace", embedding_model="minilm")
    mock_collection = MagicMock()
    with patch("mempalace.palace.get_collection", return_value=mock_collection):
        mem._write_sync(" 王先生.", "曾因破损退货", "interaction")
    metadata = mock_collection.upsert.call_args.kwargs["metadatas"][0]
    assert metadata["customer_display"] == " 王先生."
    assert metadata["wing"] == _wing(" 王先生.")
