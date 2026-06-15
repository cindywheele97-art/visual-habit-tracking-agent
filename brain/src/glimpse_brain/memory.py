"""Per-customer memory: a thin async seam with an in-memory fake for tests.
MemPalaceMemory (mempalace_memory.py) is the production impl behind this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryHit:
    text: str
    kind: str          # "interaction" | "fact"
    score: float = 0.0


class Memory(Protocol):
    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]: ...
    async def write(self, customer: str, content: str, kind: str) -> None: ...


class InMemoryMemory:
    """Test double + reference semantics. Recall = substring match (or all when
    the query matches nothing), most-recent-first, capped at k."""

    def __init__(self) -> None:
        self._store: dict[str, list[MemoryHit]] = {}

    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        hits = self._store.get(customer, [])
        matched = [h for h in hits if query and query in h.text]
        ordered = list(reversed(matched if matched else hits))
        return ordered[:k]

    async def write(self, customer: str, content: str, kind: str) -> None:
        self._store.setdefault(customer, []).append(MemoryHit(text=content, kind=kind))
