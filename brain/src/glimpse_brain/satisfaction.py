"""Pure rolling-rate satisfaction metric. Drives an advisory only — never an
action. Isolated and exhaustively tested, mirroring how Phase 3 isolated safety."""

from __future__ import annotations

import math
from collections import deque


class SatisfactionTracker:
    def __init__(self, *, window: int, threshold: float, min_ratings: int) -> None:
        self._verdicts: deque[str] = deque(maxlen=window)
        self._threshold = threshold
        self._min_ratings = min_ratings
        # Minimum denominator: the smallest sample count at which exactly
        # min_ratings "up" verdicts can reach the threshold rate.
        # e.g. threshold=0.8, min_ratings=3 → ceil(3/0.8)=4, so you need ≥4
        # items before 3 ups can score ≥0.8 (3/3=1.0 would fire too early).
        self._fill_min: int = math.ceil(min_ratings / threshold) if threshold > 0 else min_ratings
        self._advised = False

    @property
    def rate(self) -> float:
        if not self._verdicts:
            return 0.0
        ups = sum(1 for v in self._verdicts if v == "up")
        denom = max(len(self._verdicts), self._fill_min)
        return ups / denom

    def ready(self) -> bool:
        return len(self._verdicts) >= self._min_ratings and self.rate >= self._threshold

    def record(self, verdict: str) -> bool:
        """Append a verdict; return True only on the rising edge into ready
        (fires once, then stays quiet until the rate drops and rises again)."""
        self._verdicts.append(verdict)
        if self.ready():
            if not self._advised:
                self._advised = True
                return True
            return False
        self._advised = False
        return False

    def seed(self, verdicts: list[str]) -> None:
        """Fill from replayed history; a maxlen deque keeps only the last window.
        Set advised=ready() so a relaunch while qualified does not re-advise."""
        self._verdicts.extend(verdicts)
        self._advised = self.ready()
