"""Provider-neutral tool-use seam. The Agent speaks these types only; each
client implementation translates its SDK <-> these types. Reserving this seam
makes a future OpenAI/third-party client a drop-in (common path only)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from anthropic.types import MessageParam


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    id: str          # matches a ToolCall.id
    output: str = ""
    image_b64: str = ""
    media_type: str = ""


@dataclass(frozen=True)
class ToolImage:
    """An image a tool returns (the agent sees it as an Anthropic image block)."""
    image_b64: str
    media_type: str = "image/jpeg"


@dataclass(frozen=True)
class AgentStep:
    """The model's turn output: tool calls to run, or a final answer."""

    tool_calls: tuple[ToolCall, ...] = ()
    final_text: str | None = None


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class ToolResultsMessage:
    results: tuple[ToolResult, ...]


# A neutral transcript entry the client serializes to provider messages.
TranscriptEntry = UserMessage | AgentStep | ToolResultsMessage


@runtime_checkable
class ToolSpec(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolUseClient(Protocol):
    """One tool-use turn: given the transcript + tools, return the model's step."""

    async def run_turn(
        self,
        *,
        system: str,
        transcript: list[TranscriptEntry],
        tools: Sequence[ToolSpec],
    ) -> AgentStep: ...


def _step_from_response(response: Any) -> AgentStep:
    """Anthropic response.content -> neutral AgentStep (pure; no network)."""
    tool_calls = tuple(
        ToolCall(id=block.id, name=block.name, input=dict(block.input))
        for block in response.content
        if getattr(block, "type", None) == "tool_use"
    )
    if tool_calls:
        return AgentStep(tool_calls=tool_calls)
    text = "".join(
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    )
    return AgentStep(final_text=text)


def _to_anthropic_messages(transcript: list[TranscriptEntry]) -> list[dict[str, Any]]:
    """Neutral transcript -> Anthropic messages (pure; preserves tool_use_ids)."""
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        if isinstance(entry, UserMessage):
            messages.append({"role": "user", "content": entry.text})
        elif isinstance(entry, AgentStep):
            messages.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                    for c in entry.tool_calls
                ],
            })
        elif isinstance(entry, ToolResultsMessage):
            messages.append({
                "role": "user",
                "content": [_tool_result_block(r) for r in entry.results],
            })
    return messages


def _tool_result_block(result: ToolResult) -> dict[str, Any]:
    if result.image_b64:
        return {
            "type": "tool_result",
            "tool_use_id": result.id,
            "content": [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": result.media_type or "image/jpeg",
                    "data": result.image_b64,
                },
            }],
        }
    return {"type": "tool_result", "tool_use_id": result.id, "content": result.output}


class AnthropicToolUseClient:
    """Production ToolUseClient. Constructed lazily so tests never import anthropic."""

    def __init__(self, model: str, max_tokens: int = 1024) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(timeout=30.0)
        self._model = model
        self._max_tokens = max_tokens

    async def run_turn(
        self,
        *,
        system: str,
        transcript: list[TranscriptEntry],
        tools: Sequence[ToolSpec],
    ) -> AgentStep:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ],
            messages=cast(Iterable[MessageParam], _to_anthropic_messages(transcript)),
        )
        return _step_from_response(response)
