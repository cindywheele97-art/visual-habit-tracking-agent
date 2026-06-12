from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from glimpse_brain.config import Config
from glimpse_brain.server import GlimpseServer

OCR_LINE = (
    '{"type":"ocr","seq":%d,"ts":"2026-06-11T12:00:00Z","region_id":"region-1",'
    '"blocks":[{"text":"%s","x0":0.05,"x1":0.4,"conf":0.95}]}\n'
)


class FakeLLM:
    async def complete(self, *, system: str, user: str, model: str) -> str:
        return '["好的，亲，马上处理"]'


def make_config(tmp_path: Path) -> Config:
    playbook = tmp_path / "playbook.md"
    playbook.write_text("满99包邮", encoding="utf-8")
    return Config.model_validate(
        {
            "brain": {
                "socket_path": str(tmp_path / "glimpse.sock"),
                "event_log": str(tmp_path / "events.jsonl"),
                "playbook": str(playbook),
            },
            "tracker": {"settle_ms": 30},
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
    server = GlimpseServer(cfg, llm=FakeLLM())
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
