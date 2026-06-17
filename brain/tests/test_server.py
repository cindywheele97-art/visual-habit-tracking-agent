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
        }
    )


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


async def test_server_offers_look_tool_when_image_present(tmp_path: Path) -> None:
    # WHY: an OcrMsg carrying an image makes the look tool available to the agent.
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
