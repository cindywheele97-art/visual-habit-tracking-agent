"""Agent tools for per-customer memory. Each is bound to ONE customer at
construction (the brain injects the current customer), so the model never has to
know or pass the key — it just recalls/remembers 'about the current customer'."""

from __future__ import annotations

from typing import Any

from glimpse_brain.memory import Memory


class RecallCustomerTool:
    name = "recall_customer"
    description = "回忆当前客户的历史互动和已知信息；起草回复前可调用。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "要回忆的内容或关键词"}},
        "required": [],
    }

    def __init__(self, memory: Memory, customer: str, k: int) -> None:
        self._memory = memory
        self._customer = customer
        self._k = k

    async def run(self, input: dict[str, Any]) -> str:
        hits = await self._memory.recall(self._customer, input.get("query", ""), self._k)
        if not hits:
            return "（暂无该客户的记忆）"
        return "\n".join(f"- [{h.kind}] {h.text}" for h in hits)


class RememberAboutCustomerTool:
    name = "remember_about_customer"
    description = "记住关于当前客户的一条要点（偏好、历史问题等），供以后回忆。"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "要记住的要点"}},
        "required": ["fact"],
    }

    def __init__(self, memory: Memory, customer: str) -> None:
        self._memory = memory
        self._customer = customer

    async def run(self, input: dict[str, Any]) -> str:
        fact = input.get("fact", "").strip()
        if not fact:
            return "（未提供要点）"
        await self._memory.write(self._customer, fact, "fact")
        return f"已记住：{fact}"
