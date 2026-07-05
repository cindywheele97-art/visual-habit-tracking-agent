"""Shared LLM client + rate limiter (used by agent and summarizer)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import Protocol


class LLMClient(Protocol):
    """Protocol for LLM clients."""

    async def complete(self, *, system: str, user: str, model: str) -> str: ...


class AnthropicLLM:
    """Production client. Constructed lazily so tests never import anthropic."""

    def __init__(self) -> None:
        from anthropic import AsyncAnthropic

        # a stalled call must fail fast, not hang the UI
        self._client = AsyncAnthropic(timeout=30.0)

    async def complete(self, *, system: str, user: str, model: str) -> str:
        response = await self._client.messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in response.content if block.type == "text")


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
        # Stamp is recorded at allow-time, not on LLM success: a failing call
        # still burns budget. Accepted for v1 — the window self-heals in 60s.
        self._stamps.append(now)
        return True
