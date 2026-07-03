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
        self._firing = False
        self._repoke = False

    def poke(self) -> None:
        """(Re)start the settle timer; the action fires after `delay` quiet seconds.

        A poke while the action is RUNNING never cancels it — aborting
        mid-LLM-call under a steady message stream would starve suggestions
        forever. It schedules one follow-up run (re-debounced) instead.

        Must be called from within a running event loop."""
        if self._firing:
            self._repoke = True
            return
        self.cancel()
        self._task = asyncio.get_running_loop().create_task(self._run())

    def cancel(self) -> None:
        """Abort everything, including an in-flight action — this is connection
        teardown, where the pass's output has nowhere to go anyway."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        self._firing = False
        self._repoke = False

    async def _run(self) -> None:
        await asyncio.sleep(self._delay)
        self._firing = True
        try:
            await self._action()
        except Exception:  # fire-and-forget task: a crash must be visible, not GC'd
            log.exception("settle action raised")
        finally:
            # Only clean up if we are still the current task: a cancel() +
            # fresh poke() may already have installed a replacement whose
            # state we must not clobber.
            if self._task is asyncio.current_task():
                self._firing = False
                if self._repoke:
                    self._repoke = False
                    self._task = asyncio.get_running_loop().create_task(self._run())
