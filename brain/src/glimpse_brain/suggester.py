"""Builds grounded prompts, calls the LLM, parses ranked reply suggestions."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from glimpse_brain.errors import CostCapExceeded, SuggestionParseError
from glimpse_brain.redaction import Redactor

SYSTEM_TEMPLATE = """\
你是一名资深电商客服助手，为人工客服起草候选回复。
规则：
- 每条回复都必须以下面 <playbook> 中的产品信息、政策和话术为依据。
- playbook 没有覆盖的问题，如实说明需要核实，不要编造。
- 语气友好简洁，符合中文电商客服习惯；客户用什么语言就用什么语言回复。

<playbook>
{playbook}
</playbook>"""

USER_TEMPLATE = """\
以下是最近的对话（"客户" = customer，"我" = the human agent）：

{conversation}

为"我"起草最多 {n} 条候选回复。
只输出一个 JSON 字符串数组，例如 ["...", "..."]，不要输出其他内容。"""


class LLMClient(Protocol):
    """Protocol for LLM clients."""

    async def complete(self, *, system: str, user: str, model: str) -> str: ...


class AnthropicLLM:
    """Production client. Constructed lazily so tests never import anthropic."""

    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic()

    async def complete(self, *, system: str, user: str, model: str) -> str:
        response = await self._client.messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )


class RateLimiter:
    """Enforces max calls per minute with a sliding window."""

    def __init__(
        self, max_per_minute: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_per_minute
        self._clock = clock
        self._stamps: deque[float] = deque()

    def allow(self) -> bool:
        """Check if a call is allowed; if so, record a stamp and return True."""
        now = self._clock()
        while self._stamps and now - self._stamps[0] > 60.0:
            self._stamps.popleft()
        if len(self._stamps) >= self._max:
            return False
        self._stamps.append(now)
        return True


class Suggester:
    """Generates playbook-grounded suggestions via LLM."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        model: str,
        playbook_path: Path,
        redactor: Redactor,
        limiter: RateLimiter,
        max_suggestions: int,
    ) -> None:
        self._llm = llm
        self._model = model
        self._playbook_path = playbook_path
        self._redactor = redactor
        self._limiter = limiter
        self._max = max_suggestions

    async def suggest(self, tail: list[str]) -> list[str]:
        """Generate suggestions for the given conversation tail.

        Args:
            tail: Recent conversation lines.

        Returns:
            List of suggested replies (up to max_suggestions).

        Raises:
            CostCapExceeded: If rate limit is hit.
            SuggestionParseError: If LLM output cannot be parsed.
        """
        if not self._limiter.allow():
            raise CostCapExceeded("LLM call rate cap reached")
        # Re-read every call by design: edit the playbook while Glimpse runs
        # and the next suggestion uses the new content.
        playbook = (
            self._playbook_path.read_text(encoding="utf-8")
            if self._playbook_path.exists()
            else "(playbook file missing)"
        )
        conversation = self._redactor.redact("\n".join(tail))
        raw = await self._llm.complete(
            system=SYSTEM_TEMPLATE.format(playbook=playbook),
            user=USER_TEMPLATE.format(conversation=conversation, n=self._max),
            model=self._model,
        )
        return _parse_suggestions(raw, self._max)


def _parse_suggestions(raw: str, limit: int) -> list[str]:
    """Extract and validate suggestions from LLM output.

    Looks for a JSON array in the output, validates it contains strings,
    and returns up to `limit` items.

    Raises:
        SuggestionParseError: If JSON cannot be found or parsed.
    """
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end <= start:
        raise SuggestionParseError(raw[:200])
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SuggestionParseError(str(exc)) from exc
    items = [s.strip() for s in data if isinstance(s, str) and s.strip()]
    if not items:
        raise SuggestionParseError("no usable strings in LLM output")
    return items[:limit]
