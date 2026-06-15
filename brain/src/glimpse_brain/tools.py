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
    description = (
        "获取产品信息、政策和话术。起草任何依赖这些信息的回复前应先调用。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查询的问题或关键词"}
        },
        "required": [],
    }

    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    async def run(self, input: dict[str, Any]) -> str:
        return self._knowledge.grounding(input.get("query", ""))
