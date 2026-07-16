"""Pure trajectory sessionizer (audit §三 B3).

Stamps a session_id onto every behavior event so the flywheel's atomic record
is the 选品 *trajectory* (a sequence of considered pages → outcome), not the
isolated click. Boundaries come from two sources, both deterministic code — no
LLM (Rule 5):

  - automatic: an inactivity gap longer than `idle_gap_seconds` ends a run
  - explicit: the operator's 开始/结束选品 controls give ground-truth boundaries

OS-free and time-injected (observe/begin take epoch seconds) so the boundary
logic the analytics layer trusts is fully unit-tested.
"""

from __future__ import annotations


class Sessionizer:
    def __init__(self, idle_gap_seconds: float = 900.0) -> None:
        self._idle_gap = idle_gap_seconds
        self._current: str | None = None
        self._last_ts: float | None = None

    def observe(self, ts: float) -> str:
        """Session_id for a behavior event at `ts`. Opens a new session when
        none is active or the gap since the last event exceeds idle_gap."""
        if self._current is None or (
            self._last_ts is not None and ts - self._last_ts > self._idle_gap
        ):
            self._current = self._new_id(ts)
        self._last_ts = ts
        return self._current

    def begin(self, ts: float) -> str:
        """Explicit '开始选品': force a fresh session boundary even with no gap."""
        self._current = self._new_id(ts)
        self._last_ts = ts
        return self._current

    def end(self) -> None:
        """Explicit '结束选品': close the session; the next observe opens a new one."""
        self._current = None
        self._last_ts = None

    def current(self) -> str:
        """The active session_id, or "" when none is open (e.g. an outcome
        marked before any run was started)."""
        return self._current or ""

    @staticmethod
    def _new_id(ts: float) -> str:
        # Millisecond precision from the start ts: deterministic (replayable
        # audit trail) and collision-free at human interaction rates.
        return f"sel-{int(ts * 1000)}"
