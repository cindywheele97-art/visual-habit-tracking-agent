"""Knowledge the agent retrieves via tools. An OKF catalog: the agent reads an
index (table of contents), then reads docs by id. Replaces the v1 whole-file
FileKnowledgeBase."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from glimpse_brain.okf import load_catalog


class KnowledgeBase(Protocol):
    def index(self) -> str: ...
    def read(self, doc_id: str) -> str: ...


class OkfKnowledgeBase:
    """OKF-catalog knowledge. Re-scans the catalog each call so hand-edits to the
    markdown take effect live. Falls back to a single legacy playbook.md."""

    def __init__(self, catalog_dir: Path, legacy_playbook: Path | None = None) -> None:
        self._catalog_dir = catalog_dir
        self._legacy_playbook = legacy_playbook

    def index(self) -> str:
        docs = load_catalog(self._catalog_dir, self._legacy_playbook)
        if not docs:
            return "（知识库为空）"
        ordered = sorted(docs, key=lambda d: (d.type, d.id))
        lines = [f"- [{d.type}] {d.id}: {d.description}" for d in ordered]
        return "知识库目录：\n" + "\n".join(lines)

    def read(self, doc_id: str) -> str:
        for doc in load_catalog(self._catalog_dir, self._legacy_playbook):
            if doc.id == doc_id:
                return f"# {doc.title}\n\n{doc.body}"
        return f"（未找到文档：{doc_id}）"
