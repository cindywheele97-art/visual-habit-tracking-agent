"""Knowledge grounding the agent retrieves via a tool. v1 returns whole files;
the multimodal-ready signature (query) lets later impls do real retrieval."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class KnowledgeBase(Protocol):
    def grounding(self, query: str) -> str: ...


class FileKnowledgeBase:
    """Whole-file grounding: playbook (+ learnings if present), re-read each call."""

    def __init__(self, playbook_path: Path, learnings_path: Path | None = None) -> None:
        self._playbook_path = playbook_path
        self._learnings_path = learnings_path

    def grounding(self, query: str) -> str:  # query ignored in v1 (multimodal-ready)
        playbook = (
            self._playbook_path.read_text(encoding="utf-8")
            if self._playbook_path.exists()
            else "(playbook file missing)"
        )
        parts = [f"<playbook>\n{playbook}\n</playbook>"]
        if self._learnings_path is not None and self._learnings_path.exists():
            learnings = self._learnings_path.read_text(encoding="utf-8").strip()
            if learnings:
                parts.append(f"<learnings>\n{learnings}\n</learnings>")
        return "\n".join(parts)
