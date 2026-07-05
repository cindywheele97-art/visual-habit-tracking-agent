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
