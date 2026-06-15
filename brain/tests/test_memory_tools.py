from __future__ import annotations

from glimpse_brain.memory import InMemoryMemory
from glimpse_brain.memory_tools import RecallCustomerTool, RememberAboutCustomerTool
from glimpse_brain.redaction import Redactor


async def test_recall_tool_scopes_to_bound_customer() -> None:
    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰", "fact")
    tool = RecallCustomerTool(mem, customer="小明", k=5, redactor=Redactor([]))
    out = await tool.run({"query": "顺丰"})
    assert "顺丰" in out
    # the agent never passes the customer — it's bound at construction
    assert tool.name == "recall_customer"
    assert tool.input_schema["required"] == []


async def test_recall_tool_empty_is_friendly() -> None:
    tool = RecallCustomerTool(InMemoryMemory(), customer="新客户", k=5, redactor=Redactor([]))
    out = await tool.run({"query": "x"})
    assert "暂无" in out


async def test_remember_tool_writes_a_fact() -> None:
    mem = InMemoryMemory()
    tool = RememberAboutCustomerTool(mem, customer="小明", redactor=Redactor([]))
    out = await tool.run({"fact": "对包装要求高"})
    assert "已记住" in out
    hits = await mem.recall("小明", "包装", k=5)
    assert any(h.kind == "fact" and "包装" in h.text for h in hits)


async def test_remember_tool_ignores_empty_fact() -> None:
    mem = InMemoryMemory()
    tool = RememberAboutCustomerTool(mem, customer="小明", redactor=Redactor([]))
    out = await tool.run({})
    assert await mem.recall("小明", "", k=5) == []
    assert "未提供" in out


async def test_remember_tool_redacts_pii_before_storing() -> None:
    # WHY: a fact the model writes (possibly parroting a phone number from the
    # conversation) must be redacted before it's persisted.
    mem = InMemoryMemory()
    tool = RememberAboutCustomerTool(mem, customer="小明", redactor=Redactor([r"1[3-9]\d{9}"]))
    await tool.run({"fact": "客户电话13812345678，偏好顺丰"})
    hits = await mem.recall("小明", "顺丰", k=5)
    assert hits and "13812345678" not in hits[0].text


async def test_recall_tool_redacts_pii_before_returning() -> None:
    # WHY: recalled content reaches the model; PII must not leak even if some
    # legacy stored content was unredacted.
    mem = InMemoryMemory()
    await mem.write("小明", "电话13812345678", "interaction")  # unredacted on purpose
    tool = RecallCustomerTool(mem, customer="小明", k=5, redactor=Redactor([r"1[3-9]\d{9}"]))
    out = await tool.run({"query": "电话"})
    assert "13812345678" not in out
