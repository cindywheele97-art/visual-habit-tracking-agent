"""Async debounce: fire an action only after the input has gone quiet."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


class SettleGate:
    def __init__(self, delay: float, action: Callable[[], Awaitable[None]]) -> None:
        self._delay = delay
        self._action = action
        self._task: asyncio.Task[None] | None = None

    def poke(self) -> None:
        """(Re)start the settle timer; the action fires after `delay` quiet seconds.

        Must be called from within a running event loop."""
        self.cancel()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(self._delay)
        try:
            await self._action()
        except Exception:  # fire-and-forget task: a crash must be visible, not GC'd
            log.exception("settle action raised")
