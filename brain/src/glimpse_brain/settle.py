"""Async debounce: fire an action only after the input has gone quiet."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class SettleGate:
    def __init__(self, delay: float, action: Callable[[], Awaitable[None]]) -> None:
        self._delay = delay
        self._action = action
        self._task: asyncio.Task[None] | None = None

    def poke(self) -> None:
        """(Re)start the settle timer; the action fires after `delay` quiet seconds."""
        self.cancel()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(self._delay)
        await self._action()
