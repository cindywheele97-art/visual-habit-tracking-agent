"""Tests for the playbook-grounded suggestion engine."""

from __future__ import annotations

from pathlib import Path

import pytest

from glimpse_brain.errors import CostCapExceeded, SuggestionParseError
from glimpse_brain.redaction import Redactor
from glimpse_brain.suggester import RateLimiter, Suggester


class FakeLLM:
    def __init__(self, reply: str = '["好的，马上为您处理", "请稍等哦"]') -> None:
        self.reply = reply
        self.calls: list[dict[str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append({"system": system, "user": user, "model": model})
        return self.reply


def make_suggester(tmp_path: Path, llm: FakeLLM, max_per_minute: int = 10) -> Suggester:
    playbook = tmp_path / "playbook.md"
    playbook.write_text("# 政策\n全场满99包邮，七天无理由退换。", encoding="utf-8")
    return Suggester(
        llm=llm,
        model="claude-sonnet-4-6",
        playbook_path=playbook,
        redactor=Redactor([r"1[3-9]\d{9}"]),
        limiter=RateLimiter(max_per_minute),
        max_suggestions=3,
    )


async def test_suggestions_grounded_in_playbook(tmp_path: Path) -> None:
    # WHY: ungrounded suggestions don't match the business — grounding is the
    # whole point of the playbook file (spec decision #4).
    llm = FakeLLM()
    s = make_suggester(tmp_path, llm)
    out = await s.suggest(["客户: 包邮吗？"])
    assert out == ["好的，马上为您处理", "请稍等哦"]
    assert "满99包邮" in llm.calls[0]["system"]
    assert "包邮吗" in llm.calls[0]["user"]


async def test_conversation_is_redacted_before_llm(tmp_path: Path) -> None:
    # WHY: privacy hard rule — only redacted text reaches the LLM (spec §5).
    llm = FakeLLM()
    s = make_suggester(tmp_path, llm)
    await s.suggest(["客户: 我电话13812345678"])
    assert "13812345678" not in llm.calls[0]["user"]


async def test_cost_cap_raises(tmp_path: Path) -> None:
    llm = FakeLLM()
    s = make_suggester(tmp_path, llm, max_per_minute=1)
    await s.suggest(["客户: 在吗"])
    with pytest.raises(CostCapExceeded):
        await s.suggest(["客户: 在吗？？"])


async def test_garbage_llm_output_raises_parse_error(tmp_path: Path) -> None:
    llm = FakeLLM(reply="抱歉我帮不了你")
    s = make_suggester(tmp_path, llm)
    with pytest.raises(SuggestionParseError):
        await s.suggest(["客户: 在吗"])


async def test_json_extracted_from_chatty_output(tmp_path: Path) -> None:
    llm = FakeLLM(reply='这是建议:\n["回复一"]\n希望有帮助')
    s = make_suggester(tmp_path, llm)
    assert await s.suggest(["客户: 在吗"]) == ["回复一"]


async def test_max_suggestions_enforced(tmp_path: Path) -> None:
    llm = FakeLLM(reply='["a", "b", "c", "d", "e"]')
    s = make_suggester(tmp_path, llm)
    assert len(await s.suggest(["客户: 在吗"])) == 3


def test_rate_limiter_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, clock=lambda: now[0])
    assert limiter.allow() and limiter.allow()
    assert not limiter.allow()
    now[0] = 61.0  # old stamps age out of the 60s window
    assert limiter.allow()


async def test_missing_playbook_degrades_not_crashes(tmp_path: Path) -> None:
    # WHY: a misconfigured playbook path must not kill suggestions silently —
    # the sentinel makes the gap visible in the prompt (and in logs).
    llm = FakeLLM()
    s = Suggester(
        llm=llm,
        model="claude-sonnet-4-6",
        playbook_path=tmp_path / "nope.md",
        redactor=Redactor([]),
        limiter=RateLimiter(10),
        max_suggestions=3,
    )
    await s.suggest(["客户: 在吗"])
    assert "(playbook file missing)" in llm.calls[0]["system"]
