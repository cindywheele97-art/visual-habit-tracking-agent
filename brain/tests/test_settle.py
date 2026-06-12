from __future__ import annotations

import asyncio

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
