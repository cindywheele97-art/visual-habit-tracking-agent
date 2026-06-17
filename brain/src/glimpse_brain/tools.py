"""Tools the agent composes. Each Tool advertises an Anthropic-compatible schema
and runs async. KnowledgeBaseTool is the first; Memory/Vision register later."""

from __future__ import annotations

from typing import Any, Protocol

from glimpse_brain.knowledge import KnowledgeBase
from glimpse_brain.tooluse import ToolImage


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    async def run(self, input: dict[str, Any]) -> "str | ToolImage": ...


class KnowledgeBaseTool:
    name = "knowledge_base"
    description = "返回知识库目录（各文档 id/类型/摘要）；先调用它了解有哪些资料，再用 read_knowledge 读取相关文档。"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    async def run(self, input: dict[str, Any]) -> str:
        return self._knowledge.index()


class ReadKnowledgeTool:
    name = "read_knowledge"
    description = "按 id 读取某篇知识库文档的完整内容。可并行读取多篇。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "文档 id（见 knowledge_base 目录）"}
        },
        "required": ["id"],
    }

    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    async def run(self, input: dict[str, Any]) -> str:
        return self._knowledge.read(input.get("id", ""))
