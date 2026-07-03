from __future__ import annotations

import asyncio
import logging

import pytest

from glimpse_brain.settle import SettleGate


async def test_fires_once_after_quiet_period() -> None:
    # WHY: a customer firing 3 messages in a row must produce ONE suggestion
    # pass, not three LLM calls (spec §4.3).
    count = 0

    async def action() -> None:
        nonlocal count
        count += 1

    gate = SettleGate(delay=0.05, action=action)
    gate.poke()
    await asyncio.sleep(0.01)
    gate.poke()  # burst: second poke before settle resets the timer
    await asyncio.sleep(0.01)
    gate.poke()
    await asyncio.sleep(0.15)
    assert count == 1


async def test_poke_does_not_cancel_in_flight_action() -> None:
    # WHY: a poke mid-LLM-call must not abort the pass — under a steady stream
    # of messages that starves suggestions forever. The running pass finishes;
    # the poke schedules ONE follow-up pass afterwards (which sees the newest
    # messages).
    started = asyncio.Event()
    finish = asyncio.Event()
    completed = 0

    async def action() -> None:
        nonlocal completed
        started.set()
        await finish.wait()
        completed += 1

    gate = SettleGate(delay=0.01, action=action)
    gate.poke()
    await started.wait()
    gate.poke()  # lands while the action is running
    gate.poke()  # burst mid-flight still debounces to one follow-up
    finish.set()
    await asyncio.sleep(0.1)
    assert completed == 2  # the in-flight pass finished AND one follow-up ran


async def test_cancel_prevents_fire() -> None:
    fired = False

    async def action() -> None:
        nonlocal fired
        fired = True

    gate = SettleGate(delay=0.03, action=action)
    gate.poke()
    gate.cancel()
    await asyncio.sleep(0.08)
    assert not fired


async def test_action_exception_is_logged_not_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # WHY: fire-and-forget tasks otherwise swallow crashes until GC prints
    # "Task exception was never retrieved" — the failure must be visible.
    async def boom() -> None:
        raise RuntimeError("kaboom")

    gate = SettleGate(delay=0.01, action=boom)
    with caplog.at_level(logging.ERROR):
        gate.poke()
        await asyncio.sleep(0.05)
    assert "settle action raised" in caplog.text
