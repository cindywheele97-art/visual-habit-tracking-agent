"""Pure trajectory sessionizer (audit §三 B3).

Stamps a session_id onto every behavior event so the flywheel's atomic record
is the 选品 *trajectory* (a sequence of considered pages → outcome), not the
isolated click. Deterministic code, no LLM (Rule 5). OS-free and time-injected
so the boundary semantics the analytics layer trusts are fully unit-tested.

Semantics (converged over six adversarial review rounds — each rule exists
because its absence was a reproduced corruption):

- CLICKS (`observe`) are active ground truth in FIFO order: they open runs and
  ASSIGN the recency clock (a future-skewed ts corrupts ≤1 boundary decision,
  then self-corrects). EXCEPT inside the marking window (below), where an
  implicit click is flow noise: the marking UI itself requires tab-focus
  clicks, and letting them open phantom runs hijacked anchors and stole
  verdicts. Cost, accepted: post-end browsing without 开始选品 is unattributed
  until the window expires.
- DWELLS (`observe_span`) are passive and systematically ~one poll tick late.
  Detached (began beyond the idle gap of last activity): "" and inert. A span
  whose midpoint predates the current run's open belongs to the PREDECESSOR
  run (the begin control always beats the previous run's final reading dwell
  to the socket) and never advances the new run's clock. Otherwise it labels
  the open run and extends recency to its end; with no run open it labels the
  last run without touching the (frozen) clock.
- 开始/结束选品 (`begin`/`end`) are operator ground truth. `end(ts)` opens the
  MARKING WINDOW anchored at the close; `bind_outcome(at)` binds a verdict and
  SLIDES the window (a long marking batch stays bound; abandonment expires
  it). `begin` closes the window — verdicts then belong to the new run. A
  double 结束选品 is a no-op.
- Restart: `snapshot()`/`rehydrate()` persist the state VERBATIM — rebuilding
  it from event rows was a lossy projection (a whole class of gate failures).
"""

from __future__ import annotations


class Sessionizer:
    def __init__(self, idle_gap_seconds: float = 900.0) -> None:
        self._idle_gap = idle_gap_seconds
        self._current: str | None = None
        self._current_opened: float | None = None
        self._prev_id = ""  # run superseded by the current one (span labeling)
        self._last_id = ""  # most recent run id, retained across end()
        self._last_ts: float | None = None
        # (run_id, anchor_ts) of the marking window opened by 结束选品; the
        # anchor slides on each bound verdict. Cleared by begin().
        self._ended: tuple[str, float] | None = None
        self._seq = 0

    # ---- active events (clicks) ---------------------------------------------

    def observe(self, ts: float) -> str:
        """Session_id for an active behavior event (click) at `ts`."""
        if self._in_marking_window(ts):
            return ""  # tab-focus flow noise: no run, no side effects
        if self._current is None or (
            self._last_ts is not None and ts - self._last_ts > self._idle_gap
        ):
            self._open(ts)
        self._last_ts = ts  # active = recency ground truth: assign, self-correcting
        return self._current or self._last_id

    # ---- passive events (dwells) --------------------------------------------

    def observe_span(self, start: float, end: float) -> str:
        """Session_id for a passive attention interval [start, end]."""
        if self._last_ts is None or start - self._last_ts > self._idle_gap:
            return ""  # detached: never opens, never advances, never labels
        if self._current is not None:
            mid = (start + end) / 2.0
            if self._current_opened is not None and mid < self._current_opened:
                # The span belongs to the predecessor: the begin/open control
                # systematically beats the closing dwell to the socket.
                return self._prev_id or self._last_id
            self._last_ts = max(self._last_ts, end)  # advance only; never rewind
            return self._current
        return self._last_id  # closed run: trailing attention, clock frozen

    # ---- operator controls ---------------------------------------------------

    def begin(self, ts: float) -> str:
        """Explicit '开始选品': force a fresh boundary; verdicts now belong here."""
        self._open(ts)
        self._last_ts = ts
        self._ended = None
        return self._current or ""

    def end(self, ts: float) -> None:
        """Explicit '结束选品': close the run and open the marking window
        anchored at `ts`. No-op when no run is open (double-press safe)."""
        if self._current is None:
            return
        self._ended = (self._current, ts)
        self._prev_id = self._current
        self._current = None
        self._current_opened = None
        self._last_ts = ts if self._last_ts is None else max(self._last_ts, ts)

    def current(self) -> str:
        """The active (open) session_id, or "" when none is open."""
        return self._current or ""

    def last_session(self, *, at: float) -> str:
        """Read-only: the session a verdict at `at` would attach to.
        1. the marking window's run (explicit close outranks everything);
        2. the open run;
        3. within the gap of the last activity, the most recent run;
        4. "" — honestly sessionless."""
        if self._ended is not None and at - self._ended[1] <= self._idle_gap:
            return self._ended[0]
        if self._current is not None:
            return self._current
        if (
            self._last_id
            and self._last_ts is not None
            and at - self._last_ts <= self._idle_gap
        ):
            return self._last_id
        return ""

    def bind_outcome(self, *, at: float) -> str:
        """Bind a verdict: like last_session, but a bind to the marking
        window's run SLIDES the window — a long marking batch stays bound to
        the run it grades, while true abandonment still expires."""
        sid = self.last_session(at=at)
        if self._ended is not None and sid == self._ended[0]:
            self._ended = (sid, at)
        return sid

    # ---- restart --------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Verbatim state for persistence (rebuilding from event rows is a
        lossy projection — merge-gate finding class)."""
        return {
            "current": self._current,
            "current_opened": self._current_opened,
            "prev_id": self._prev_id,
            "last_id": self._last_id,
            "last_ts": self._last_ts,
            "ended_id": self._ended[0] if self._ended else None,
            "ended_ts": self._ended[1] if self._ended else None,
            "seq": self._seq,
        }

    def rehydrate(
        self,
        *,
        current: str | None,
        last_id: str,
        last_ts: float | None,
        ended_id: str | None,
        ended_ts: float | None,
        seq: int,
        current_opened: float | None = None,
        prev_id: str = "",
    ) -> None:
        """Restore a snapshot() verbatim after a brain restart. Call before
        any observe."""
        self._current = current
        self._current_opened = current_opened
        self._prev_id = prev_id
        self._last_id = last_id
        self._last_ts = last_ts
        self._ended = (ended_id, ended_ts) if ended_id and ended_ts is not None else None
        self._seq = seq

    # ---- internals -------------------------------------------------------------

    def _in_marking_window(self, ts: float) -> bool:
        return (
            self._current is None
            and self._ended is not None
            and ts - self._ended[1] <= self._idle_gap
        )

    def _open(self, ts: float) -> None:
        # Millisecond precision + a per-instance sequence: deterministic across a
        # fresh replay (first session at a given ts is identical) yet unique even
        # when two runs start in the same whole-second shell timestamp.
        self._prev_id = self._current or self._last_id
        self._current = f"sel-{int(ts * 1000)}-{self._seq}"
        self._current_opened = ts
        self._last_id = self._current
        self._seq += 1
