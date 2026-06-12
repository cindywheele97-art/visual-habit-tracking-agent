"""Turns raw OCR snapshots into conversation state and "what's new"."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from glimpse_brain.protocol import Block

Side = Literal["in", "out"]
_LABELS: dict[Side, str] = {"in": "客户", "out": "我"}


@dataclass
class IngestResult:
    accepted: bool
    new_inbound: list[str] = field(default_factory=list)
    new_outbound: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class _Line:
    text: str
    side: Side


class ConversationTracker:
    def __init__(
        self,
        *,
        min_confidence: float,
        side_threshold: float,
        ignore_patterns: list[str],
        max_seen: int = 2000,
        max_conversation: int = 200,
    ) -> None:
        self._min_conf = min_confidence
        self._side_threshold = side_threshold
        self._ignore = [re.compile(p) for p in ignore_patterns]
        self._max_seen = max_seen
        self._max_conversation = max_conversation
        # Design: _seen is deliberate global-lifetime dedup (no TTL). A text
        # seen once never re-triggers — even if the customer repeats it days
        # later — because duplicate suggestions cost more trust than a missed
        # repeat. Bounded by max_seen (FIFO eviction). Revisit if real usage
        # shows missed repeats matter.
        self._seen: dict[str, None] = {}  # insertion-ordered set
        self._last_texts: list[str] = []
        self._conversation: list[_Line] = []

    def ingest(self, blocks: list[Block]) -> IngestResult:
        if not blocks:
            return IngestResult(accepted=False, reason="empty")
        kept = self._filter_blocks(blocks)
        if not kept:
            return IngestResult(accepted=True, reason="empty-after-filter")
        mean_conf = sum(block.conf for block, _ in kept) / len(kept)
        if mean_conf < self._min_conf:
            return IngestResult(accepted=False, reason="low-confidence")

        lines = [
            _Line(text=text, side=self._side_of(block)) for block, text in kept
        ]
        texts = [line.text for line in lines]
        if texts == self._last_texts:
            return IngestResult(accepted=True, reason="unchanged")
        self._last_texts = texts

        # New-tail scan: walk up from the bottom until we hit a line we've seen.
        # Appended messages are unseen tail lines; scroll-back puts a seen line
        # at the bottom, yielding an empty tail.
        new_tail: list[_Line] = []
        for line in reversed(lines):
            if line.text in self._seen:
                break
            new_tail.append(line)
        new_tail.reverse()

        for line in new_tail:
            self._remember(line.text)
            self._conversation.append(line)
        if len(self._conversation) > self._max_conversation:
            del self._conversation[: -self._max_conversation]

        return IngestResult(
            accepted=True,
            new_inbound=[line.text for line in new_tail if line.side == "in"],
            new_outbound=[line.text for line in new_tail if line.side == "out"],
        )

    def tail(self, n: int = 12) -> list[str]:
        return [f"{_LABELS[line.side]}: {line.text}" for line in self._conversation[-n:]]

    def _filter_blocks(self, blocks: list[Block]) -> list[tuple[Block, str]]:
        """Normalize text and drop empty/ignored blocks BEFORE the confidence
        gate, so a low-confidence timestamp can't veto real messages."""
        kept: list[tuple[Block, str]] = []
        for block in blocks:
            text = " ".join(block.text.split())
            if not text or any(p.search(text) for p in self._ignore):
                continue
            kept.append((block, text))
        return kept

    def _side_of(self, block: Block) -> Side:
        # Known limitation (v1): side is assigned per OCR line, not per chat
        # bubble. A wrapped bubble whose lines have different x-centroids can
        # split one logical message across "in"/"out". Bubble-aware grouping
        # is deferred; the settle gate + suggester tail soften the impact.
        center = (block.x0 + block.x1) / 2
        return "in" if center < self._side_threshold else "out"

    def _remember(self, text: str) -> None:
        self._seen[text] = None
        if len(self._seen) > self._max_seen:
            self._seen.pop(next(iter(self._seen)))
