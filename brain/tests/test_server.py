from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from glimpse_brain.config import Config
from glimpse_brain.server import GlimpseServer
from glimpse_brain.tooluse import AgentStep, ToolCall

OCR_LINE = (
    '{"type":"ocr","seq":%d,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
    '"blocks":[{"text":"%s","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
)


class FakeLLM:
    async def complete(self, *, system: str, user: str, model: str) -> str:
        return '["好的，亲，马上处理"]'


class FakeToolClient:
    """Always finalizes immediately with two drafts (no tool calls)."""

    async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
        return AgentStep(final_text='["好的，亲，马上处理", "请稍等哦"]')


def make_config(tmp_path: Path) -> Config:
    playbook = tmp_path / "playbook.md"
    playbook.write_text("满99包邮", encoding="utf-8")
    return Config.model_validate(
        {
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(playbook),
                "feedback_log": str(tmp_path / "feedback.jsonl"),
                "knowledge_dir": str(tmp_path / "knowledge"),
            },
            "tracker": {"settle_ms": 30},
            "memory": {"enabled": False},
            "sku": {"enabled": False},
        }
    )


async def read_next_non_ack(
    reader: asyncio.StreamReader, timeout: float = 2.0
) -> dict[str, Any]:
    """The next substantive message — used to assert what arrives FIRST."""

    async def _scan() -> dict[str, Any]:
        while True:
            line = await reader.readline()
            assert line, "connection closed unexpectedly"
            msg: dict[str, Any] = json.loads(line)
            if msg["type"] != "ack":
                return msg

    return await asyncio.wait_for(_scan(), timeout)


async def read_until(
    reader: asyncio.StreamReader, msg_type: str, timeout: float = 2.0
) -> dict[str, Any]:
    async def _scan() -> dict[str, Any]:
        while True:
            line = await reader.readline()
            assert line, "connection closed unexpectedly"
            msg: dict[str, Any] = json.loads(line)
            if msg["type"] == msg_type:
                return msg

    return await asyncio.wait_for(_scan(), timeout)


async def test_happy_path_and_no_duplicate_suggestions(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        assert (await read_until(reader, "status"))["state"] == "watching"

        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 1
        sug = await read_until(reader, "suggestions")
        assert sug["items"][0]["text"] == "好的，亲，马上处理"

        # Same screen re-OCR'd: ack arrives, but NO second suggestion may fire.
        writer.write((OCR_LINE % (2, "在吗，包邮吗？")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 2
        with pytest.raises(asyncio.TimeoutError):
            await read_until(reader, "suggestions", timeout=0.3)

        # Event log captured the lifecycle.
        kinds = [
            json.loads(line)["kind"]
            for line in Path(cfg.brain.event_log)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert "observation" in kinds and "suggestion_shown" in kinds
        writer.close()
    finally:
        task.cancel()


async def test_reconnect_does_not_cancel_new_connections_settle(tmp_path: Path) -> None:
    # WHY: the old connection's cleanup must cancel only ITS OWN settle gate.
    # If it cancels the new connection's gate, a reconnect racing a pending
    # suggestion silently kills suggestions — no crash, just loss.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader1, writer1 = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer1.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer1.drain()
        await read_until(reader1, "suggestions")  # first connection fully settled

        reader2, writer2 = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer2.write((OCR_LINE % (2, "什么时候发货？")).encode())
        await writer2.drain()
        assert (await read_until(reader2, "ack"))["seq"] == 2  # new gate poked

        writer1.close()  # old handler unwinds DURING the new settle window
        sug = await read_until(reader2, "suggestions", timeout=1.0)
        assert sug["items"]
        writer2.close()
    finally:
        task.cancel()


CLICK_LINE = (
    '{"type":"click","ts":"2026-06-12T09:00:00Z","app":"com.google.Chrome",'
    '"x":12.0,"y":34.0,"blocks":[{"text":"Adidas Ultraboost","x0":0.1,"x1":0.5,"conf":0.9}]}\n'
)


async def test_copied_message_logged(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        _, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"copied","suggestion_id":"s1","region_id":"region-1"}\n')
        await writer.drain()
        await asyncio.sleep(0.1)
        log = Path(cfg.brain.event_log).read_text(encoding="utf-8")
        assert "suggestion_copied" in log
        writer.close()
    finally:
        task.cancel()


async def test_click_is_logged_as_click_event(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        _, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(CLICK_LINE.encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        records = [
            json.loads(line)
            for line in Path(cfg.brain.event_log).read_text(encoding="utf-8").splitlines()
        ]
        clicks = [r for r in records if r["kind"] == "click"]
        assert len(clicks) == 1
        assert clicks[0]["payload"]["app"] == "com.google.Chrome"
        assert clicks[0]["payload"]["texts"] == ["Adidas Ultraboost"]
        writer.close()
    finally:
        task.cancel()


async def test_summarize_request_returns_summary(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)

    class SummaryLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            return "今天你在看 Adidas。"

    server = GlimpseServer(cfg, llm=SummaryLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(CLICK_LINE.encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(b'{"type":"summarize"}\n')
        await writer.drain()
        msg = await read_until(reader, "summary")
        assert "Adidas" in msg["text"]
        writer.close()
    finally:
        task.cancel()


async def test_summary_returns_status_to_watching(tmp_path: Path) -> None:
    # WHY: summarize leaves the overlay stuck on "thinking" unless the brain resets status.
    cfg = make_config(tmp_path)

    class SummaryLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            return "今天总结。"

    server = GlimpseServer(cfg, llm=SummaryLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(CLICK_LINE.encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(b'{"type":"summarize"}\n')
        await writer.drain()
        assert (await read_until(reader, "status"))["state"] == "thinking"
        await read_until(reader, "summary")
        assert (await read_until(reader, "status"))["state"] == "watching"
        writer.close()
    finally:
        task.cancel()


async def test_summarize_does_not_block_ocr_processing(tmp_path: Path) -> None:
    # WHY: summarize can take 30s — if it blocks _dispatch, live OCR acks stall and the shell re-sends forever.
    cfg = make_config(tmp_path)
    release = asyncio.Event()

    class BlockingSummaryLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            await release.wait()
            return "今天总结。"

    server = GlimpseServer(
        cfg, llm=BlockingSummaryLLM(), tool_client=FakeToolClient()
    )
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(CLICK_LINE.encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        writer.write(b'{"type":"summarize"}\n')
        await writer.drain()
        await asyncio.sleep(0.05)  # let summarize task start and block on LLM
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 1
        sug = await read_until(reader, "suggestions")
        assert sug["items"][0]["text"] == "好的，亲，马上处理"
        release.set()
        summary = await read_until(reader, "summary")
        assert summary["text"] == "今天总结。"
        writer.close()
    finally:
        task.cancel()


async def test_replied_message_logged(tmp_path: Path) -> None:
    # WHY: a feature that sends messages to real customers must leave a record of
    # what was sent and how (fill/sent/cancelled). The audit line proves it.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        _, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(
            b'{"type":"replied","suggestion_id":"s1","region_id":"region-1","mode":"sent"}\n'
        )
        await writer.drain()
        await asyncio.sleep(0.1)
        records = [
            json.loads(line)
            for line in Path(cfg.brain.event_log).read_text(encoding="utf-8").splitlines()
        ]
        replied = [r for r in records if r["kind"] == "replied"]
        assert len(replied) == 1
        assert replied[0]["payload"]["mode"] == "sent"
        assert replied[0]["payload"]["suggestion_id"] == "s1"
        writer.close()
    finally:
        task.cancel()


async def test_ocr_click_and_summarize_interleave(tmp_path: Path) -> None:
    # WHY: clicks must not disturb the OCR suggestion path, and a summarize
    # after both must still return — the highest-risk untested interaction.
    cfg = make_config(tmp_path)

    class SummaryLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            # Suggestions now come from the tool_client (the Agent); this
            # LLMClient.complete path only serves the summarizer.
            return "今天你在看 Adidas。"

    server = GlimpseServer(cfg, llm=SummaryLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        # OCR → suggestion
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        # click mid-session
        writer.write(CLICK_LINE.encode())
        await writer.drain()
        await asyncio.sleep(0.1)
        # summarize still works
        writer.write(b'{"type":"summarize"}\n')
        await writer.drain()
        msg = await read_until(reader, "summary")
        assert msg["text"]
        # the click was logged alongside the observation
        kinds = [
            json.loads(line)["kind"]
            for line in Path(cfg.brain.event_log).read_text(encoding="utf-8").splitlines()
        ]
        assert "click" in kinds and "observation" in kinds
        writer.close()
    finally:
        task.cancel()


async def test_server_captures_interactions_and_passes_customer(tmp_path: Path) -> None:
    # WHY: with a known contact, the brain auto-captures the interaction to that
    # customer's memory and scopes the agent to them.
    from glimpse_brain.memory import InMemoryMemory

    cfg = make_config(tmp_path)
    mem = InMemoryMemory()

    class CustomerCapturingClient:
        def __init__(self) -> None:
            self.seen_customer_had_memory = False

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.seen_customer_had_memory = any(t.name == "recall_customer" for t in tools)
            return AgentStep(final_text='["好的"]')

    client = CustomerCapturingClient()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client, memory=mem)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","contact":"小明",'
                '"blocks":[{"text":"在吗，包邮吗？","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert client.seen_customer_had_memory  # agent got memory tools for 小明
        recalled = await mem.recall("小明", "包邮", k=5)
        assert any(h.kind == "interaction" for h in recalled)  # captured
        writer.close()
    finally:
        task.cancel()


async def test_agent_drives_suggestions_and_logs_agent_turn(tmp_path: Path) -> None:
    # WHY: the agent replaces the suggester on the suggestion path; its turn is
    # auditable (tools used + draft count, logged before the user sees drafts)
    # and the full tool-call -> tool-result -> finalize path works through the server.

    class ToolThenFinalizeClient:
        def __init__(self) -> None:
            self.turns = 0

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.turns += 1
            if self.turns == 1:
                return AgentStep(
                    tool_calls=(ToolCall(id="t1", name="knowledge_base", input={"query": "包邮"}),)
                )
            return AgentStep(final_text='["好的，亲，满99包邮", "请稍等哦"]')

    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=ToolThenFinalizeClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        sug = await read_until(reader, "suggestions")
        assert sug["items"][0]["text"] == "好的，亲，满99包邮"
        records = [
            json.loads(line)
            for line in Path(cfg.brain.event_log).read_text(encoding="utf-8").splitlines()
        ]
        kinds = [r["kind"] for r in records]
        assert kinds.index("agent_turn") < kinds.index("suggestion_shown")
        agent_turn = next(r for r in records if r["kind"] == "agent_turn")
        assert agent_turn["payload"]["tools_used"] == ["knowledge_base"]
        assert agent_turn["payload"]["draft_count"] == 2
        writer.close()
    finally:
        task.cancel()


async def test_server_withholds_look_tool_by_default(tmp_path: Path) -> None:
    # WHY: the README promises only redacted text reaches the LLM. With the
    # default config, an OcrMsg image must NOT put look_at_conversation on the
    # table — otherwise unredacted screenshot pixels get uploaded.
    cfg = make_config(tmp_path)

    class ToolSnoop:
        def __init__(self) -> None:
            self.saw_look = False

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.saw_look = any(t.name == "look_at_conversation" for t in tools)
            return AgentStep(final_text='["好的"]')

    client = ToolSnoop()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","image":"QUJD",'
                '"blocks":[{"text":"这个还有吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert not client.saw_look
        writer.close()
    finally:
        task.cancel()


async def test_server_offers_look_tool_when_image_present_and_opted_in(tmp_path: Path) -> None:
    # WHY: an OcrMsg carrying an image makes the look tool available to the
    # agent — but only once the user opted in to sending screenshots.
    cfg = make_config(tmp_path)
    cfg.llm.send_images = True

    class ToolSnoop:
        def __init__(self) -> None:
            self.saw_look = False

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.saw_look = any(t.name == "look_at_conversation" for t in tools)
            return AgentStep(final_text='["好的"]')

    client = ToolSnoop()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","image":"QUJD",'
                '"blocks":[{"text":"这个还有吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert client.saw_look
        writer.close()
    finally:
        task.cancel()


async def test_new_activity_marks_shown_suggestions_stale(tmp_path: Path) -> None:
    # WHY: the stale flag is the auto-send safety gate (SendPlanner downgrades
    # fillThenSend to fill-only when stale). It protects no one unless the brain
    # actually produces it: the moment new conversation activity is observed,
    # the cards still on screen were drafted against an outdated conversation
    # and must be flagged BEFORE the fresh drafts eventually arrive.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        first = await read_until(reader, "suggestions")
        assert first["stale"] is False

        # A new customer message lands while the old cards are still on screen.
        writer.write((OCR_LINE % (2, "什么时候发货？")).encode())
        await writer.drain()
        marked = await read_until(reader, "suggestions")
        assert marked["stale"] is True
        assert marked["items"] == first["items"]  # same cards, now flagged

        # The fresh drafts for the new message arrive afterwards, not stale.
        fresh = await read_until(reader, "suggestions")
        assert fresh["stale"] is False

        # WHY (second cycle): the producer must re-arm after every fresh round.
        # If the stale_sent latch survived the snapshot swap, the gate would be
        # silently dead from the second round onward.
        writer.write((OCR_LINE % (3, "有优惠券吗？")).encode())
        await writer.drain()
        marked2 = await read_until(reader, "suggestions")
        assert marked2["stale"] is True
        fresh2 = await read_until(reader, "suggestions")
        assert fresh2["stale"] is False
        writer.close()
    finally:
        task.cancel()


OCR_OUT_LINE = (
    '{"type":"ocr","seq":%d,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
    '"blocks":[{"text":"%s","x0":0.62,"x1":0.9,"conf":0.95}]}\n'
)


OCR_CONTACT_LINE = (
    '{"type":"ocr","seq":%d,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
    '"contact":"%s",'
    '"blocks":[{"text":"%s","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
)


async def test_customer_switch_resets_conversation_context(tmp_path: Path) -> None:
    # WHY: switching chats swaps the human on the other end. Without a reset,
    # customer A's messages ride into customer B's prompt — cross-customer
    # contamination of drafts (and of any memory written from that tail).
    from glimpse_brain.tooluse import AgentStep

    class PromptSnoop:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.prompts.append(transcript[0].text)
            return AgentStep(final_text='["好的"]')

    snoop = PromptSnoop()
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=snoop)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_CONTACT_LINE % (1, "小明", "我要退货")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert "我要退货" in snoop.prompts[0]

        writer.write((OCR_CONTACT_LINE % (2, "老王", "这个多少钱")).encode())
        await writer.drain()
        marked = await read_until(reader, "suggestions")
        assert marked["stale"] is True  # 小明's on-screen cards get flagged
        await read_until(reader, "suggestions")  # 老王's fresh round
        assert "这个多少钱" in snoop.prompts[1]
        assert "我要退货" not in snoop.prompts[1]  # 小明's context must not leak
        writer.close()
    finally:
        task.cancel()


async def test_returning_to_a_chat_restores_context_and_detects_repeats(tmp_path: Path) -> None:
    # WHY: conversation state must be PER customer. Revisiting a chat has to
    # restore its tail (drafting context) — and a message whose text another
    # chat used before must still count as new in this one.
    from glimpse_brain.tooluse import AgentStep

    class PromptSnoop:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.prompts.append(transcript[0].text)
            # Distinct from every customer message in this test: a draft equal
            # to an incoming line would (correctly) trip the fill-echo filter.
            return AgentStep(final_text='["收到，马上为您处理"]')

    snoop = PromptSnoop()
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=snoop)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_CONTACT_LINE % (1, "小明", "在吗")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")

        writer.write((OCR_CONTACT_LINE % (2, "老王", "好的")).encode())
        await writer.drain()
        assert (await read_until(reader, "suggestions"))["stale"] is True
        await read_until(reader, "suggestions")  # 老王's fresh round

        # Back to 小明, who has JUST sent 好的 — a text 老王 used before.
        line = (
            '{"type":"ocr","seq":3,"ts":"t","region_id":"region-1","contact":"小明",'
            '"blocks":[{"text":"在吗","x0":0.05,"x1":0.4,"conf":0.95},'
            '{"text":"好的","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
        )
        writer.write(line.encode())
        await writer.drain()
        assert (await read_until(reader, "suggestions"))["stale"] is True
        await read_until(reader, "suggestions")
        prompt = snoop.prompts[2]
        assert "好的" in prompt  # the repeat was detected as new
        assert "在吗" in prompt  # 小明's earlier context was restored
        writer.close()
    finally:
        task.cancel()


async def test_transient_blank_contact_does_not_leak_context(tmp_path: Path) -> None:
    # WHY: a single failed contact read between two chats (A → "" → B) must
    # not stitch their conversations together — the switch must still be
    # recognized when the next known name differs.
    from glimpse_brain.tooluse import AgentStep

    class PromptSnoop:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.prompts.append(transcript[0].text)
            return AgentStep(final_text='["好的"]')

    snoop = PromptSnoop()
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=snoop)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_CONTACT_LINE % (1, "小明", "我要退货")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")

        # Same frame content, contact read failed for one frame.
        writer.write((OCR_LINE % (2, "我要退货")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 2

        writer.write((OCR_CONTACT_LINE % (3, "老王", "这个多少钱")).encode())
        await writer.drain()
        assert (await read_until(reader, "suggestions"))["stale"] is True
        await read_until(reader, "suggestions")
        assert "这个多少钱" in snoop.prompts[1]
        assert "我要退货" not in snoop.prompts[1]  # 小明's context must not leak
        writer.close()
    finally:
        task.cancel()


async def test_fill_echo_of_shown_suggestion_does_not_mark_stale(tmp_path: Path) -> None:
    # WHY: when the watch region covers the input box, the shell's own fill is
    # OCR'd back as a "new outbound line" 1-2s into the 5s countdown. Treating
    # that echo as the conversation moving on would stale-cancel the very send
    # it belongs to — auto-send would deterministically self-cancel.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        sug = await read_until(reader, "suggestions")

        # The user clicks 发送: the draft lands in the input box and is OCR'd back.
        writer.write((OCR_OUT_LINE % (2, sug["items"][0]["text"])).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 2
        writer.write(b'{"type":"summarize"}\n')
        await writer.drain()
        # If the echo had (wrongly) produced a stale marker, it would arrive
        # before the summarize request's status reply. (Not hello — hello
        # deliberately clears the snapshot for the resurrection guard.)
        assert (await read_next_non_ack(reader))["type"] == "status"

        # A genuinely new outbound line still marks the cards stale.
        writer.write((OCR_OUT_LINE % (3, "这单我手动回了")).encode())
        await writer.drain()
        marked = await read_until(reader, "suggestions")
        assert marked["stale"] is True
        writer.close()
    finally:
        task.cancel()


async def test_new_connection_does_not_resurrect_old_cards(tmp_path: Path) -> None:
    # WHY: a hello means a fresh shell session with an EMPTY overlay. Re-sending
    # a previous connection's snapshot as a stale marker would render outdated —
    # possibly another customer's — drafts one 填入 click from the input box.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        writer.close()

        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        assert (await read_until(reader, "status"))["state"] == "watching"
        writer.write((OCR_LINE % (2, "什么时候发货？")).encode())
        await writer.drain()
        # First suggestions on the fresh session must be the fresh drafts —
        # never a stale re-broadcast of cards this shell never displayed.
        sug = await read_until(reader, "suggestions")
        assert sug["stale"] is False
        writer.close()
    finally:
        task.cancel()


async def test_drafts_computed_before_new_activity_arrive_stale(tmp_path: Path) -> None:
    # WHY: a poke never cancels an in-flight pass (that would starve
    # suggestions under a steady stream) — so drafts computed from a tail read
    # before mid-pass activity WILL be delivered. They are born outdated;
    # delivering them stale=false would invite auto-sending a duplicate or
    # contradictory reply. (Inbound additionally schedules a follow-up pass;
    # outbound — the human answered manually — schedules nothing.)
    from glimpse_brain.tooluse import AgentStep

    gate = asyncio.Event()

    class GatedClient:
        def __init__(self) -> None:
            self.calls = 0

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.calls += 1
            if self.calls == 1:
                await gate.wait()  # hold the first pass open mid-flight
            return AgentStep(final_text='["好的，亲，马上处理"]')

    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=GatedClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 1
        await asyncio.sleep(0.1)  # settle (30ms) elapsed → the pass is in flight

        # The human replies manually while the agent is still drafting.
        writer.write((OCR_OUT_LINE % (2, "包邮的哦")).encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 2

        gate.set()  # let the in-flight pass finish
        sug = await read_until(reader, "suggestions")
        assert sug["stale"] is True  # born outdated → never auto-sendable
        writer.close()
    finally:
        task.cancel()


async def test_empty_image_frame_clears_retained_screenshot(tmp_path: Path) -> None:
    # WHY: an over-budget frame ships image="" as fail-soft-to-TEXT. Retaining
    # the previous frame's screenshot would make the agent reason (match_sku /
    # look) over a stale — possibly another conversation's — image.
    cfg = make_config(tmp_path)
    cfg.llm.send_images = True

    class ToolSnoop:
        def __init__(self) -> None:
            self.saw_look_per_turn: list[bool] = []

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.saw_look_per_turn.append(
                any(t.name == "look_at_conversation" for t in tools)
            )
            return AgentStep(final_text='["好的"]')

    client = ToolSnoop()
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=client)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        line = ('{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","image":"QUJD",'
                '"blocks":[{"text":"这个还有吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n')
        writer.write(line.encode())
        await writer.drain()
        await read_until(reader, "suggestions")

        writer.write((OCR_LINE % (2, "多少钱？")).encode())  # image="" frame
        await writer.drain()
        stale_marker = await read_until(reader, "suggestions")
        assert stale_marker["stale"] is True  # round 1 flagged by the new activity
        await read_until(reader, "suggestions")  # round 2 (fresh)
        assert client.saw_look_per_turn == [True, False]
        writer.close()
    finally:
        task.cancel()


async def test_agent_system_prompt_matches_offered_tools(tmp_path: Path) -> None:
    # WHY: a system prompt that directs the model to a withheld tool burns loop
    # iterations on "unknown tool" and can exhaust the pass into a degraded
    # status for exactly the image-bearing messages it describes.
    from glimpse_brain.tooluse import AgentStep

    class SystemSnoop:
        def __init__(self) -> None:
            self.system = ""

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.system = system
            return AgentStep(final_text='["好的"]')

    async def observed_system(cfg: Config) -> str:
        snoop = SystemSnoop()
        server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=snoop)
        task = asyncio.create_task(server.run())
        await asyncio.sleep(0.05)
        try:
            reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
            writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
            await writer.drain()
            await read_until(reader, "suggestions")
            writer.close()
        finally:
            task.cancel()
        return snoop.system

    default_cfg = make_config(tmp_path)  # send_images off, sku + memory disabled
    system = await observed_system(default_cfg)
    assert "look_at_conversation" not in system
    assert "match_sku" not in system
    assert "recall_customer" not in system

    vision_cfg = make_config(tmp_path)
    vision_cfg.llm.send_images = True
    system = await observed_system(vision_cfg)
    assert "look_at_conversation" in system


async def test_agent_system_mentions_memory_tools_only_with_memory(tmp_path: Path) -> None:
    # WHY: same invariant as vision/sku — recall_customer/remember_about_customer
    # must only be promised when a memory backend actually exists.
    from glimpse_brain.tooluse import AgentStep

    class FakeMemory:
        async def write(self, customer: str, text: str, kind: str) -> None: ...

        async def recall(self, customer: str, query: str, k: int) -> list[str]:
            return []

    class SystemSnoop:
        def __init__(self) -> None:
            self.system = ""

        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            self.system = system
            return AgentStep(final_text='["好的"]')

    snoop = SystemSnoop()
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=snoop, memory=FakeMemory())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        assert "recall_customer" in snoop.system
        writer.close()
    finally:
        task.cancel()


async def test_server_no_contact_skips_capture(tmp_path: Path) -> None:
    # WHY: no identity → no per-customer capture (fail-soft); spec-required leg.
    from glimpse_brain.memory import InMemoryMemory

    cfg = make_config(tmp_path)
    mem = InMemoryMemory()

    class FinalizeClient:
        async def run_turn(self, *, system: str, transcript: list, tools: list) -> AgentStep:
            return AgentStep(final_text='["好的"]')

    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FinalizeClient(), memory=mem)
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        # OCR_LINE has NO "contact" field → current_customer is None
        writer.write((OCR_LINE % (1, "在吗，包邮吗？")).encode())
        await writer.drain()
        await read_until(reader, "suggestions")
        # nothing was captured under any customer
        assert await mem.recall("", "包邮", k=5) == []
        writer.close()
    finally:
        task.cancel()


async def test_feedback_writes_corpus_event_and_memory(tmp_path: Path) -> None:
    from glimpse_brain.memory import InMemoryMemory

    cfg = make_config(tmp_path)
    memory = InMemoryMemory()
    server = GlimpseServer(
        cfg, llm=FakeLLM(), tool_client=FakeToolClient(), memory=memory
    )
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")

        # An OCR with a contact sets the current customer AND fires a suggestion,
        # so a snapshot (conversation + draft) exists for the feedback to resolve.
        ocr = (
            '{"type":"ocr","seq":1,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
            '"contact":"老王",'
            '"blocks":[{"text":"能便宜点吗","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
        )
        writer.write(ocr.encode())
        await writer.drain()
        sug = await read_until(reader, "suggestions")
        sid = sug["items"][0]["id"]

        writer.write(
            (
                '{"type":"feedback","suggestion_id":"%s","region_id":"region-1",'
                '"verdict":"down","note":"强调赠品"}\n' % sid
            ).encode()
        )
        await writer.drain()
        await asyncio.sleep(0.1)

        corpus = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").strip()
        assert "强调赠品" in corpus
        assert "能便宜点吗" in corpus
        events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert '"kind": "feedback"' in events
        hits = await memory.recall("老王", "修正", k=5)
        assert any("强调赠品" in h.text for h in hits)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_ocr_with_realistic_image_size_is_processed(tmp_path: Path) -> None:
    # WHY: every OcrMsg carries a base64 region screenshot — typically well over
    # asyncio's 64 KB default readline() limit. If the server can't read such a
    # line, the FIRST real frame kills the connection and the shell's
    # resend-on-reconnect turns it into a permanent crash loop.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        image = "A" * 150_000  # ~150 KB base64, the size of a real chat region JPEG
        line = (
            '{"type":"ocr","seq":1,"ts":"t","region_id":"region-1","image":"%s",'
            '"blocks":[{"text":"在吗，包邮吗？","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
            % image
        )
        writer.write(line.encode())
        await writer.drain()
        assert (await read_until(reader, "ack"))["seq"] == 1
        sug = await read_until(reader, "suggestions")
        assert sug["items"]
        writer.close()
    finally:
        task.cancel()


async def test_oversized_line_does_not_kill_connection(tmp_path: Path) -> None:
    # WHY: an over-limit line must be dropped, not crash the handler — a closed
    # connection makes the shell reconnect and re-send the identical oversized
    # payload, disabling the whole pipeline forever.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"ocr","image":"' + b"A" * 3_000_000 + b'"}\n')
        await writer.drain()
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        # The oversized line is discarded; the connection survives and the
        # follow-up message is still served.
        assert (await read_until(reader, "status", timeout=5.0))["state"] == "watching"
        writer.close()
    finally:
        task.cancel()


def test_build_sku_disabled_returns_none(tmp_path: Path) -> None:
    from glimpse_brain.server import GlimpseServer

    cfg = make_config(tmp_path)  # sku disabled
    assert GlimpseServer._build_sku(cfg) is None


def test_build_sku_missing_files_returns_none(tmp_path: Path) -> None:
    from glimpse_brain.config import Config
    from glimpse_brain.server import GlimpseServer

    cfg = Config.model_validate(
        {
            "sku": {
                "enabled": True,
                "model_path": str(tmp_path / "nope.onnx"),
                "index_path": str(tmp_path / "nope.npz"),
            }
        }
    )
    # Missing model/index → load raises → fail-soft to None (SKU disabled).
    assert GlimpseServer._build_sku(cfg) is None


async def test_satisfaction_advisory_fires_on_threshold(tmp_path: Path) -> None:
    cfg = Config.model_validate(
        {
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(tmp_path / "pb.md"),
                "feedback_log": str(tmp_path / "feedback.jsonl"),
            },
            "tracker": {"settle_ms": 30},
            "memory": {"enabled": False},
            "feedback": {
                "satisfaction_window": 5,
                "advisory_threshold": 1.0,
                "advisory_min_ratings": 3,
            },
        }
    )
    (tmp_path / "pb.md").write_text("满99包邮", encoding="utf-8")
    server = GlimpseServer(cfg, llm=FakeLLM(), tool_client=FakeToolClient())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        reader, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(b'{"type":"hello","shell_version":"0.1.0"}\n')
        await writer.drain()
        await read_until(reader, "status")
        for _ in range(3):
            writer.write(
                b'{"type":"feedback","suggestion_id":"s1","region_id":"r",'
                b'"verdict":"up","note":""}\n'
            )
            await writer.drain()
        adv = await read_until(reader, "advisory")
        assert "自动发送" in adv["text"]
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
