from __future__ import annotations

from glimpse_brain.tools import KnowledgeBaseTool


class FakeKB:
    def grounding(self, query: str) -> str:
        return f"grounding-for:{query}"


async def test_kb_tool_advertises_schema() -> None:
    tool = KnowledgeBaseTool(FakeKB())
    assert tool.name == "knowledge_base"
    assert tool.description
    assert tool.input_schema["type"] == "object"
    assert "query" in tool.input_schema["properties"]
    assert tool.input_schema.get("required", []) == []  # query optional


async def test_kb_tool_run_returns_grounding() -> None:
    tool = KnowledgeBaseTool(FakeKB())
    out = await tool.run({"query": "包邮"})
    assert out == "grounding-for:包邮"


async def test_kb_tool_run_tolerates_missing_query() -> None:
    # WHY: the model may call the tool with no input; must not KeyError.
    tool = KnowledgeBaseTool(FakeKB())
    out = await tool.run({})
    assert out == "grounding-for:"
