from __future__ import annotations

import pytest

from glimpse_brain.agent import Agent, AgentResult
from glimpse_brain.errors import CostCapExceeded, SuggestionParseError
from glimpse_brain.redaction import Redactor
from glimpse_brain.suggester import RateLimiter
from glimpse_brain.tooluse import AgentStep, ToolCall


class FakeKB:
    def grounding(self, query: str) -> str:
        return "政策: 满99包邮"


class ScriptedClient:
    """Returns pre-scripted AgentSteps, one per run_turn call. Records the
    transcript length seen so tests can assert tool results were fed back."""

    def __init__(self, steps: list[AgentStep]) -> None:
        self._steps = steps
        self.turns = 0
        self.last_transcript_len = 0

    async def run_turn(self, *, system, transcript, tools) -> AgentStep:
        self.last_transcript_len = len(transcript)
        step = self._steps[self.turns]
        self.turns += 1
        return step


def make_agent(client, max_iterations: int = 4, max_per_minute: int = 10) -> Agent:
    return Agent(
        client=client,
        system="SYS",
        knowledge=FakeKB(),
        redactor=Redactor([r"1[3-9]\d{9}"]),
        limiter=RateLimiter(max_per_minute),
        max_suggestions=3,
        max_iterations=max_iterations,
    )


async def test_agent_calls_tool_then_finalizes() -> None:
    # WHY: the core AI-native loop — the model requests a tool, we run it and
    # feed the result back, then it produces the drafts.
    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="t1", name="knowledge_base", input={"query": "包邮"}),)),
        AgentStep(final_text='["好的，亲，满99包邮", "需要帮您下单吗"]'),
    ])
    result = await make_agent(client).suggest(["客户: 包邮吗"])
    assert isinstance(result, AgentResult)
    assert result.drafts == ["好的，亲，满99包邮", "需要帮您下单吗"]
    assert result.tools_used == ["knowledge_base"]
    assert client.turns == 2
    # transcript grew: user + assistant-step + tool-results == 3 entries on turn 2
    assert client.last_transcript_len == 3


async def test_agent_tolerates_tool_failure_and_recovers() -> None:
    # WHY: a tool raising (I/O/network) must not kill the suggestion pass — the
    # error is fed back so the model can still finalize a best-effort draft.
    class BoomKB:
        def grounding(self, query: str) -> str:
            raise RuntimeError("kb down")

    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="t1", name="knowledge_base", input={}),)),
        AgentStep(final_text='["稍等，我帮您确认一下"]'),
    ])
    agent = Agent(
        client=client, system="SYS", knowledge=BoomKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4,
    )
    result = await agent.suggest(["客户: 在吗"])
    assert result.drafts == ["稍等，我帮您确认一下"]
    assert result.tools_used == ["knowledge_base"]


async def test_agent_finalizes_without_tools() -> None:
    client = ScriptedClient([AgentStep(final_text='["在的，亲"]')])
    result = await make_agent(client).suggest(["客户: 在吗"])
    assert result.drafts == ["在的，亲"]
    assert result.tools_used == []


async def test_agent_degrades_when_never_finalizing() -> None:
    # WHY: a non-deterministic engine must never loop forever — exhausting
    # max_iterations degrades via the existing parse-error path, no hang.
    loop_step = AgentStep(tool_calls=(ToolCall(id="t", name="knowledge_base", input={}),))
    client = ScriptedClient([loop_step] * 10)
    with pytest.raises(SuggestionParseError):
        await make_agent(client, max_iterations=3).suggest(["客户: 在吗"])
    assert client.turns == 3  # capped


async def test_agent_garbage_final_text_raises_parse_error() -> None:
    client = ScriptedClient([AgentStep(final_text="抱歉帮不了")])
    with pytest.raises(SuggestionParseError):
        await make_agent(client).suggest(["客户: 在吗"])


async def test_agent_redacts_conversation_before_model(tmp_path) -> None:
    # WHY: privacy hard rule — only redacted text reaches the model.
    captured = {}

    class CapturingClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["transcript"] = transcript
            return AgentStep(final_text='["ok"]')

    await make_agent(CapturingClient()).suggest(["客户: 我电话13812345678"])
    user_text = captured["transcript"][0].text
    assert "13812345678" not in user_text


async def test_agent_respects_rate_cap() -> None:
    client = ScriptedClient([AgentStep(final_text='["a"]'), AgentStep(final_text='["b"]')])
    agent = make_agent(client, max_per_minute=1)
    await agent.suggest(["客户: 在吗"])
    with pytest.raises(CostCapExceeded):
        await agent.suggest(["客户: 在吗？"])
