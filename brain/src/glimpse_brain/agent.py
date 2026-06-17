"""The agentic core: a Claude tool-use loop that drafts candidate replies.
Replaces Suggester on the suggestion path. Provider-neutral via ToolUseClient."""

from __future__ import annotations

from dataclasses import dataclass

from glimpse_brain.errors import CostCapExceeded, SuggestionParseError
from glimpse_brain.knowledge import KnowledgeBase
from glimpse_brain.parsing import parse_suggestions
from glimpse_brain.llm import RateLimiter
from glimpse_brain.memory import Memory
from glimpse_brain.memory_tools import RecallCustomerTool, RememberAboutCustomerTool
from glimpse_brain.tools import KnowledgeBaseTool, ReadKnowledgeTool, Tool
from glimpse_brain.tooluse import (
    AgentStep,
    ToolImage,
    ToolResult,
    ToolResultsMessage,
    ToolUseClient,
    TranscriptEntry,
    UserMessage,
)
from glimpse_brain.vision_tool import LookTool
from glimpse_brain.redaction import Redactor

USER_TEMPLATE = """\
以下是最近的对话（"客户" = customer，"我" = the human agent）：

{conversation}

为"我"起草最多 {n} 条候选回复。只输出一个 JSON 字符串数组，不要输出其他内容。"""


@dataclass(frozen=True)
class AgentResult:
    drafts: list[str]
    tools_used: list[str]


class Agent:
    """Owns the tool-use loop. `suggest` returns drafts + the tools the agent used."""

    def __init__(
        self,
        *,
        client: ToolUseClient,
        system: str,
        knowledge: KnowledgeBase,
        redactor: Redactor,
        limiter: RateLimiter,
        max_suggestions: int,
        max_iterations: int,
        memory: Memory | None = None,
        recall_k: int = 5,
    ) -> None:
        self._client = client
        self._system = system
        self._redactor = redactor
        self._limiter = limiter
        self._max = max_suggestions
        self._max_iterations = max_iterations
        self._memory = memory
        self._recall_k = recall_k
        self._base_tools: list[Tool] = [
            KnowledgeBaseTool(knowledge),
            ReadKnowledgeTool(knowledge),
        ]

    async def suggest(
        self, tail: list[str], customer: str | None = None, image: str | None = None
    ) -> AgentResult:
        if not self._limiter.allow():
            raise CostCapExceeded("agent turn rate cap reached")
        tools: list[Tool] = list(self._base_tools)
        if self._memory is not None and customer:
            tools.append(RecallCustomerTool(self._memory, customer, self._recall_k, self._redactor))
            tools.append(RememberAboutCustomerTool(self._memory, customer, self._redactor))
        # Unlike memory (optional infrastructure → `self._memory is not None`),
        # vision needs no subsystem: the image is passed straight into LookTool.
        if image:
            tools.append(LookTool(image))
        registry = {t.name: t for t in tools}
        conversation = self._redactor.redact("\n".join(tail))
        transcript: list[TranscriptEntry] = [
            UserMessage(text=USER_TEMPLATE.format(conversation=conversation, n=self._max))
        ]
        tools_used: list[str] = []
        for _ in range(self._max_iterations):
            step: AgentStep = await self._client.run_turn(
                system=self._system, transcript=transcript, tools=tools
            )
            if step.final_text is not None:
                return AgentResult(
                    drafts=parse_suggestions(step.final_text, self._max),
                    tools_used=tools_used,
                )
            transcript.append(step)
            results = []
            for call in step.tool_calls:
                tools_used.append(call.name)
                tool = registry.get(call.name)
                if tool is None:
                    output = f"unknown tool: {call.name}"
                else:
                    try:
                        output = await tool.run(call.input)
                    except Exception as exc:  # a tool failure must not kill the pass
                        output = f"tool error: {exc}"
                if isinstance(output, ToolImage):
                    results.append(ToolResult(
                        id=call.id, image_b64=output.image_b64, media_type=output.media_type
                    ))
                else:
                    results.append(ToolResult(id=call.id, output=output))
            transcript.append(ToolResultsMessage(results=tuple(results)))
        raise SuggestionParseError("agent did not finalize within max_iterations")
