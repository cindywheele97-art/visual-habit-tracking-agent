from __future__ import annotations

from glimpse_brain.memory import InMemoryMemory, MemoryHit


async def test_write_then_recall_returns_content() -> None:
    mem = InMemoryMemory()
    await mem.write("小明", "曾因 SKU-A 破损退货", "fact")
    hits = await mem.recall("小明", "破损", k=5)
    assert any(isinstance(h, MemoryHit) and "破损" in h.text for h in hits)


async def test_recall_is_scoped_per_customer() -> None:
    # WHY: the dossier boundary is the whole point — one customer's memory must
    # never surface for another.
    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰", "fact")
    assert await mem.recall("小红", "顺丰", k=5) == []


async def test_recall_respects_k_and_returns_recent_first() -> None:
    mem = InMemoryMemory()
    for i in range(5):
        await mem.write("小明", f"互动{i}", "interaction")
    hits = await mem.recall("小明", "互动", k=2)
    assert len(hits) == 2
    assert hits[0].text == "互动4"  # most recent first
