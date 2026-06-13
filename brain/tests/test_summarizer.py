from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glimpse_brain.errors import CostCapExceeded
from glimpse_brain.llm import RateLimiter
from glimpse_brain.redaction import Redactor
from glimpse_brain.summarizer import Summarizer

NOW = datetime(2026, 6, 12, 18, 0, 0, tzinfo=UTC)


class FakeLLM:
    def __init__(self, reply: str = "今天你主要在看 Adidas 跑鞋。") -> None:
        self.reply = reply
        self.calls: list[dict[str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append({"system": system, "user": user, "model": model})
        return self.reply


def _write(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _click(ts: str, app: str, texts: list[str]) -> dict[str, object]:
    return {"ts": ts, "kind": "click", "region_id": "", "payload": {"app": app, "x": 1.0, "y": 2.0, "texts": texts}}


def make_summarizer(tmp_path: Path, llm: FakeLLM, log: Path, max_per_minute: int = 10) -> Summarizer:
    return Summarizer(
        llm=llm,
        model="claude-sonnet-4-6",
        event_log=log,
        redactor=Redactor([r"1[3-9]\d{9}"]),
        limiter=RateLimiter(max_per_minute),
    )


async def test_summary_grounded_in_clicked_text(tmp_path: Path) -> None:
    # WHY: the summary must reflect what was actually clicked, not invented.
    log = tmp_path / "events.jsonl"
    _write(log, [
        _click("2026-06-12T09:00:00Z", "com.google.Chrome", ["Adidas Ultraboost", "¥899"]),
        _click("2026-06-12T09:05:00Z", "com.google.Chrome", ["Adidas 跑鞋 评价"]),
    ])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    out = await s.summarize(NOW)
    assert out == "今天你主要在看 Adidas 跑鞋。"
    assert "Adidas Ultraboost" in llm.calls[0]["user"] or "Adidas Ultraboost" in llm.calls[0]["system"]


async def test_only_today_local_clicks_included(tmp_path: Path) -> None:
    # WHY: "today" means since local midnight; yesterday's clicks must not leak in.
    log = tmp_path / "events.jsonl"
    _write(log, [
        _click("2026-06-11T09:00:00Z", "com.google.Chrome", ["YESTERDAY ITEM"]),
        _click("2026-06-12T09:00:00Z", "com.google.Chrome", ["TODAY ITEM"]),
    ])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    await s.summarize(NOW)
    blob = llm.calls[0]["user"] + llm.calls[0]["system"]
    assert "TODAY ITEM" in blob
    assert "YESTERDAY ITEM" not in blob


async def test_empty_text_clicks_skipped(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    _write(log, [_click("2026-06-12T09:00:00Z", "com.apple.Safari", [])])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    out = await s.summarize(NOW)
    assert out == "今天还没有追踪到任何活动。"
    assert llm.calls == []  # no LLM call


async def test_no_log_file_returns_fixed_string(tmp_path: Path) -> None:
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, tmp_path / "missing.jsonl")
    out = await s.summarize(NOW)
    assert out == "今天还没有追踪到任何活动。"
    assert llm.calls == []


async def test_clicked_text_is_redacted_before_llm(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    _write(log, [_click("2026-06-12T09:00:00Z", "com.google.Chrome", ["联系 13812345678"])])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    await s.summarize(NOW)
    blob = llm.calls[0]["user"] + llm.calls[0]["system"]
    assert "13812345678" not in blob


async def test_cost_cap_raises(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    _write(log, [_click("2026-06-12T09:00:00Z", "com.google.Chrome", ["item"])])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log, max_per_minute=0)
    with pytest.raises(CostCapExceeded):
        await s.summarize(NOW)


async def test_non_click_kinds_ignored(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    _write(log, [
        {"ts": "2026-06-12T09:00:00Z", "kind": "observation", "region_id": "r", "payload": {"inbound": ["NOISE"]}},
        _click("2026-06-12T09:01:00Z", "com.google.Chrome", ["REAL CLICK"]),
    ])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    await s.summarize(NOW)
    blob = llm.calls[0]["user"] + llm.calls[0]["system"]
    assert "REAL CLICK" in blob
    assert "NOISE" not in blob


async def test_naive_timestamp_does_not_crash(tmp_path: Path) -> None:
    # WHY: an external/hand-written log line with a tz-naive ts must be handled,
    # not crash the whole summary with a naive-vs-aware comparison TypeError.
    log = tmp_path / "events.jsonl"
    _write(log, [
        {"ts": "2026-06-12T09:00:00", "kind": "click", "region_id": "",
         "payload": {"app": "com.google.Chrome", "x": 1.0, "y": 2.0, "texts": ["NAIVE TS ITEM"]}},
    ])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    await s.summarize(NOW)  # must not raise
    assert llm.calls  # item was in-window and LLM was called


async def test_digest_capped_per_app(tmp_path: Path) -> None:
    # WHY: a heavy day must not blow the token budget; only recent N snippets listed.
    log = tmp_path / "events.jsonl"
    _write(log, [
        _click("2026-06-12T09:00:00Z", "com.google.Chrome", [f"ITEM{i}"])
        for i in range(120)
    ])
    llm = FakeLLM()
    s = make_summarizer(tmp_path, llm, log)
    await s.summarize(NOW)
    blob = llm.calls[0]["system"] + llm.calls[0]["user"]
    assert "ITEM119" in blob       # most recent kept
    assert "ITEM0" not in blob      # oldest dropped (cap = 50)
    assert "(120 次点击)" in blob   # true total still reported
