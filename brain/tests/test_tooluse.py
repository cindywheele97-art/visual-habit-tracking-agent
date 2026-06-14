from __future__ import annotations

from types import SimpleNamespace

from glimpse_brain.tooluse import (
    AgentStep,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    UserMessage,
    _step_from_response,
    _to_anthropic_messages,
)


def test_step_from_response_extracts_tool_calls() -> None:
    # WHY: the loop must detect tool_use blocks to know it should run tools.
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id="t1", name="kb", input={"query": "x"})]
    )
    step = _step_from_response(resp)
    assert step.final_text is None
    assert step.tool_calls == (ToolCall(id="t1", name="kb", input={"query": "x"}),)


def test_step_from_response_extracts_final_text() -> None:
    # WHY: when the model stops calling tools, its text is the final draft payload.
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text='["回复一"]')]
    )
    step = _step_from_response(resp)
    assert step.tool_calls == ()
    assert step.final_text == '["回复一"]'


def test_to_anthropic_messages_roundtrips_transcript() -> None:
    # WHY: the neutral transcript must serialize to valid Anthropic messages,
    # preserving tool_use_id correlation between assistant calls and tool results.
    transcript = [
        UserMessage(text="客户: 在吗"),
        AgentStep(tool_calls=(ToolCall(id="t1", name="kb", input={"query": "在吗"}),)),
        ToolResultsMessage(results=(ToolResult(id="t1", output="政策内容"),)),
    ]
    msgs = _to_anthropic_messages(transcript)
    assert msgs[0] == {"role": "user", "content": "客户: 在吗"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[1]["content"][0]["id"] == "t1"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0] == {
        "type": "tool_result", "tool_use_id": "t1", "content": "政策内容"
    }
