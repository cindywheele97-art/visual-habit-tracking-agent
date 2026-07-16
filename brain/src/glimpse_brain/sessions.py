"""Pure trajectory sessionizer (audit §三 B3).

Stamps a session_id onto every behavior event so the flywheel's atomic record
is the 选品 *trajectory* (a sequence of considered pages → outcome), not the
isolated click. Deterministic code, no LLM (Rule 5). OS-free and time-injected
so the boundary semantics the analytics layer trusts are fully unit-tested.

Semantics (converged over four adversarial review rounds — each rule below
exists because its absence was a reproduced corruption):

- CLICKS (`observe`) are active ground truth and arrive in FIFO emission
  order: they open runs (no run open, or idle gap exceeded) and ASSIGN the
  recency clock unconditionally — one future-skewed timestamp corrupts at most
  one boundary decision, then self-corrects.
- DWELLS (`observe_span`) are passive and systematically late (emitted when
  the interval closes). A span that BEGAN within the idle gap of the last
  activity is a continuation: it labels the run and, while the run is OPEN,
  extends recency to its end (proof of continuous attention; can never rewind).
  A span that began beyond the gap is detached: honestly unattributed (""),
  zero side effects — it must not resurrect a dead run, merge trajectories, or
  re-arm verdict binding. Scroll-only trajectories therefore stay coarse until
  the P7.4 extension tier.
- 开始/结束选品 (`begin`/`end`) are operator ground truth. `end(ts)` records the
  explicit close as the grace-window anchor: a verdict within the idle gap of
  that close binds to the closed run EVEN IF a stray click opened an implicit
  run in between (marking requires focusing the product tab, which itself
  clicks). `begin` clears that preference — verdicts then belong to the new run.
- Restart: state is reconstructible from the behavior store; `rehydrate()`
  restores it so a brain restart neither orphans a prompt verdict nor splits
  an in-flight run.
"""

from __future__ import annotations


class Sessionizer:
    def __init__(self, idle_gap_seconds: float = 900.0) -> None:
        self._idle_gap = idle_gap_seconds
        self._current: str | None = None
        self._last_id = ""  # most recent run id, retained across end()
        self._last_ts: float | None = None
        # (run_id, close_ts) of the most recent EXPLICIT 结束选品 — the verdict
        # grace anchor. Cleared by begin(); expires after idle_gap.
        self._ended: tuple[str, float] | None = None
        self._seq = 0

    # ---- active events (clicks) ---------------------------------------------

    def observe(self, ts: float) -> str:
        """Session_id for an active behavior event (click) at `ts`."""
        if self._current is None or (
            self._last_ts is not None and ts - self._last_ts > self._idle_gap
        ):
            self._open(ts)
        self._last_ts = ts  # active = recency ground truth: assign, self-correcting
        return self._current or self._last_id

    # ---- passive events (dwells) --------------------------------------------

    def observe_span(self, start: float, end: float) -> str:
        """Session_id for a passive attention interval [start, end].

        Continuation (started within the idle gap of last activity): labels the
        current-or-last run; extends the clock to `end` only while a run is
        OPEN — a closed run's grace window stays anchored at its close.
        Detached (started beyond the gap, or no activity ever): returns "" with
        zero side effects."""
        if self._last_ts is None or start - self._last_ts > self._idle_gap:
            return ""  # detached: never opens, never advances, never labels
        sid = self._current or self._last_id
        if self._current is not None:
            self._last_ts = max(self._last_ts, end)  # advance only; never rewind
        return sid

    # ---- operator controls ---------------------------------------------------

    def begin(self, ts: float) -> str:
        """Explicit '开始选品': force a fresh boundary; verdicts now belong here."""
        self._open(ts)
        self._last_ts = ts
        self._ended = None
        return self._current or ""

    def end(self, ts: float) -> None:
        """Explicit '结束选品': close the run and anchor the verdict grace window
        at `ts` — the operator asserts the run existed until this moment (the
        preceding stretch may be click-free scroll-reading). No-op if no run."""
        if self._current is None:
            return
        self._ended = (self._current, ts)
        self._current = None
        self._last_ts = ts if self._last_ts is None else max(self._last_ts, ts)

    def current(self) -> str:
        """The active (open) session_id, or "" when none is open."""
        return self._current or ""

    def last_session(self, *, at: float) -> str:
        """The session a verdict at time `at` attaches to, in priority order:
        1. a run explicitly ended within the idle gap of `at` — the deliberate
           close outranks any implicit run a stray tab-focus click opened;
        2. the open run (explicit begins cleared the preference in rule 1);
        3. within the gap of the last activity, the most recent run;
        4. "" — honestly sessionless (never mislabel a long-dead run)."""
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

    # ---- restart --------------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """Verbatim state for persistence. Reconstructing this from event rows
        is a LOSSY projection (merge-gate findings: it cannot hold an open
        implicit run AND the _ended anchor at once, and row timestamps diverge
        from the live clock) — so the live state itself is what gets stored."""
        return {
            "current": self._current,
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
    ) -> None:
        """Restore a snapshot() verbatim after a brain restart, so an in-flight
        run continues (no split), a prompt post-restart verdict still binds to
        the deliberately closed run (no orphan, no phantom steal), and new run
        ids cannot collide with pre-restart ones. Call before any observe."""
        self._current = current
        self._last_id = last_id
        self._last_ts = last_ts
        self._ended = (ended_id, ended_ts) if ended_id and ended_ts is not None else None
        self._seq = seq

    def _open(self, ts: float) -> None:
        # Millisecond precision + a per-instance sequence: deterministic across a
        # fresh replay (first session at a given ts is identical) yet unique even
        # when two runs start in the same whole-second shell timestamp.
        self._current = f"sel-{int(ts * 1000)}-{self._seq}"
        self._last_id = self._current
        self._seq += 1
