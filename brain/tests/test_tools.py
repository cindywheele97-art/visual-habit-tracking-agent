from __future__ import annotations

from glimpse_brain.tools import KnowledgeBaseTool, ReadKnowledgeTool


class FakeKB:
    def index(self) -> str:
        return "知识库目录：\n- [policy] shipping: 包邮"

    def read(self, doc_id: str) -> str:
        return f"read:{doc_id}"


async def test_knowledge_base_tool_returns_index() -> None:
    tool = KnowledgeBaseTool(FakeKB())
    assert tool.name == "knowledge_base"
    assert tool.input_schema.get("required", []) == []  # no args needed
    out = await tool.run({})
    assert "知识库目录" in out


async def test_read_knowledge_tool_reads_by_id() -> None:
    tool = ReadKnowledgeTool(FakeKB())
    assert tool.name == "read_knowledge"
    assert tool.input_schema["properties"]["id"]["type"] == "string"
    assert tool.input_schema["required"] == ["id"]
    out = await tool.run({"id": "shipping"})
    assert out == "read:shipping"


async def test_read_knowledge_tool_tolerates_missing_id() -> None:
    # WHY: the model may emit the tool call with no input; must not KeyError.
    tool = ReadKnowledgeTool(FakeKB())
    out = await tool.run({})
    assert out == "read:"
