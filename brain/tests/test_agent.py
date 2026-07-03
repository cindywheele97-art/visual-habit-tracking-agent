from __future__ import annotations

import pytest

from glimpse_brain.agent import Agent, AgentResult
from glimpse_brain.errors import CostCapExceeded, SuggestionParseError
from glimpse_brain.redaction import Redactor
from glimpse_brain.llm import RateLimiter
from glimpse_brain.tooluse import AgentStep, ToolCall


class FakeKB:
    def index(self) -> str:
        return "政策: 满99包邮"

    def read(self, doc_id: str) -> str:
        return f"read:{doc_id}"


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
        def index(self) -> str:
            raise RuntimeError("kb down")

        def read(self, doc_id: str) -> str:
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


async def test_agent_uses_memory_tools_when_customer_present() -> None:
    # WHY: with a known customer, the agent can recall that customer's memory and
    # ground the draft in it — the core of per-customer memory.
    from glimpse_brain.memory import InMemoryMemory
    from glimpse_brain.tooluse import AgentStep, ToolCall

    mem = InMemoryMemory()
    await mem.write("小明", "偏好顺丰快递", "fact")

    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="r1", name="recall_customer", input={"query": "快递"}),)),
        AgentStep(final_text='["好的小明，依旧给您发顺丰"]'),
    ])
    agent = Agent(
        client=client, system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, memory=mem, recall_k=5,
    )
    result = await agent.suggest(["客户: 还是老地址发货吧"], customer="小明")
    assert result.drafts == ["好的小明，依旧给您发顺丰"]
    assert "recall_customer" in result.tools_used


async def test_agent_omits_memory_tools_without_customer() -> None:
    # WHY: no identity → fail-soft to KB-only (pure P4); memory tools not offered.
    from glimpse_brain.memory import InMemoryMemory
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureToolsClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["tool_names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureToolsClient(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, memory=InMemoryMemory(), recall_k=5,
    )
    await agent.suggest(["客户: 在吗"], customer=None)
    assert captured["tool_names"] == ["knowledge_base", "read_knowledge"]  # no memory tools


async def test_agent_omits_memory_tools_when_memory_disabled() -> None:
    # WHY: the other fail-soft leg — a known customer but no memory backend
    # (memory=None) must also degrade to KB-only, never error.
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureToolsClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["tool_names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureToolsClient(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, memory=None, recall_k=5,
    )
    await agent.suggest(["客户: 在吗"], customer="小明")
    assert captured["tool_names"] == ["knowledge_base", "read_knowledge"]


async def test_agent_looks_at_image_when_available() -> None:
    # WHY: with an image available, the agent can call look_at_conversation, see
    # it (image tool-result fed back), and finalize a grounded reply.
    from glimpse_brain.tooluse import AgentStep, ToolCall

    fed_back: dict = {}

    class LookThenFinalize:
        def __init__(self) -> None:
            self.turns = 0

        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            self.turns += 1
            if self.turns == 1:
                return AgentStep(tool_calls=(ToolCall(id="l1", name="look_at_conversation", input={}),))
            fed_back["last"] = transcript[-1]  # the ToolResultsMessage we built
            return AgentStep(final_text='["看到了，这款是经典款黑色"]')

    agent = Agent(
        client=LookThenFinalize(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, send_images=True,
    )
    result = await agent.suggest(["客户: 这个还有货吗"], image="QUJD")
    assert result.drafts == ["看到了，这款是经典款黑色"]
    assert "look_at_conversation" in result.tools_used
    img_result = fed_back["last"].results[0]
    assert img_result.image_b64 == "QUJD"


async def test_agent_withholds_look_tool_unless_opted_in() -> None:
    # WHY: the screenshot contains unredacted pixels (faces, addresses, numbers
    # the regex layer can't touch). It may reach the LLM only when send_images
    # is explicitly enabled — an image being available is not consent.
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureTools:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureTools(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4,
    )
    await agent.suggest(["客户: 在吗"], image="QUJD")
    assert "look_at_conversation" not in captured["names"]


async def test_agent_omits_look_tool_without_image() -> None:
    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureTools:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureTools(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4,
    )
    await agent.suggest(["客户: 在吗"], image=None)
    assert "look_at_conversation" not in captured["names"]


async def test_agent_uses_index_then_read() -> None:
    from glimpse_brain.agent import Agent
    from glimpse_brain.tooluse import AgentStep, ToolCall
    from glimpse_brain.llm import RateLimiter
    from glimpse_brain.redaction import Redactor

    class FakeKB:
        def __init__(self) -> None:
            self.read_calls: list[str] = []

        def index(self) -> str:
            return "知识库目录：\n- [policy] shipping: 包邮"

        def read(self, doc_id: str) -> str:
            self.read_calls.append(doc_id)  # spy: proves the read tool ran
            return f"read:{doc_id}"

    class ScriptedClient:
        def __init__(self) -> None:
            self._calls = 0

        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            self._calls += 1
            if self._calls == 1:
                return AgentStep(tool_calls=(ToolCall(id="t1", name="knowledge_base", input={}),))
            if self._calls == 2:
                return AgentStep(tool_calls=(ToolCall(id="t2", name="read_knowledge", input={"id": "shipping"}),))
            return AgentStep(final_text='["好的，亲，满99包邮哦"]')

    kb = FakeKB()
    agent = Agent(
        client=ScriptedClient(),
        system="sys",
        knowledge=kb,
        redactor=Redactor([]),
        limiter=RateLimiter(60),
        max_suggestions=3,
        max_iterations=5,
    )
    result = await agent.suggest(["客户: 包邮吗"])
    # The agent loop appends every called tool NAME to tools_used BEFORE the
    # registry lookup, so "read_knowledge" in tools_used would be true even if the
    # tool were unregistered. The spy proves the tool was actually FOUND and RUN —
    # which only happens once ReadKnowledgeTool is registered in _base_tools.
    assert kb.read_calls == ["shipping"]
    assert "knowledge_base" in result.tools_used
    assert result.drafts == ["好的，亲，满99包邮哦"]


async def test_agent_matches_sku_then_reads_doc() -> None:
    import base64

    from glimpse_brain.tooluse import AgentStep, ToolCall

    class FakeMatcher:
        def __init__(self) -> None:
            self.calls = 0

        def match(self, jpeg_bytes: bytes):
            self.calls += 1
            return [("example-a", 0.82)]

    matcher = FakeMatcher()
    client = ScriptedClient([
        AgentStep(tool_calls=(ToolCall(id="m1", name="match_sku", input={}),)),
        AgentStep(tool_calls=(ToolCall(id="r1", name="read_knowledge", input={"id": "example-a"}),)),
        AgentStep(final_text='["这是示例商品A，亲～"]'),
    ])
    agent = Agent(
        client=client, system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=5, sku=matcher,
    )
    img = base64.b64encode(b"jpeg").decode()
    result = await agent.suggest(["客户: 这是什么型号"], image=img)
    assert matcher.calls == 1  # match_sku actually executed (registered + run)
    assert "match_sku" in result.tools_used
    assert result.drafts == ["这是示例商品A，亲～"]


async def test_agent_omits_match_sku_without_matcher() -> None:
    import base64

    from glimpse_brain.tooluse import AgentStep

    captured = {}

    class CaptureToolsClient:
        async def run_turn(self, *, system, transcript, tools) -> AgentStep:
            captured["tool_names"] = [t.name for t in tools]
            return AgentStep(final_text='["在的"]')

    agent = Agent(
        client=CaptureToolsClient(), system="SYS", knowledge=FakeKB(),
        redactor=Redactor([]), limiter=RateLimiter(10),
        max_suggestions=3, max_iterations=4, sku=None, send_images=True,
    )
    await agent.suggest(["客户: 在吗"], image=base64.b64encode(b"jpeg").decode())
    assert "match_sku" not in captured["tool_names"]  # no matcher → not offered
    assert "look_at_conversation" in captured["tool_names"]  # but vision (opted in) still is
