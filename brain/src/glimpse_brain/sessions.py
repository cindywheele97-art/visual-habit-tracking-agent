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
        self._last_id: str = ""  # most recent run id, retained across end()
        self._last_ts: float | None = None
        self._seq = 0

    def observe(self, ts: float, *, opens: bool = True) -> str:
        """Session_id for a behavior event at `ts`.

        `opens=True` (clicks — active intent): starts a new session when none is
        active or the idle gap since the last activity is exceeded.
        `opens=False` (dwells — passive evidence): attaches to the active run
        (or the last one) and never opens/splits a run — a backdated dwell that
        closed after its span must not fabricate a boundary.

        The recency clock is MONOTONIC: an out-of-order (older) event never
        rewinds `_last_ts`, so it can neither split a run nor inflate the next
        gap (review finding — dwells arrive after the clicks in their span)."""
        if opens and (
            self._current is None
            or (self._last_ts is not None and ts - self._last_ts > self._idle_gap)
        ):
            self._open(ts)
        self._last_ts = ts if self._last_ts is None else max(self._last_ts, ts)
        return self._current or self._last_id

    def begin(self, ts: float) -> str:
        """Explicit '开始选品': force a fresh session boundary even with no gap."""
        self._open(ts)
        self._last_ts = ts if self._last_ts is None else max(self._last_ts, ts)
        return self._current or ""

    def end(self) -> None:
        """Explicit '结束选品': close the run. `last_session()` still resolves to
        it so a verdict marked right after can bind to the run it describes."""
        self._current = None

    def current(self) -> str:
        """The active (open) session_id, or "" when none is open."""
        return self._current or ""

    def last_session(self) -> str:
        """The session an outcome attaches to: the active run, or the most
        recently closed one — "" only when no run has ever started."""
        return self._current or self._last_id

    def _open(self, ts: float) -> None:
        # Millisecond precision + a per-instance sequence: deterministic across a
        # fresh replay (first session at a given ts is identical) yet unique even
        # when two runs start in the same whole-second shell timestamp.
        self._current = f"sel-{int(ts * 1000)}-{self._seq}"
        self._last_id = self._current
        self._seq += 1
