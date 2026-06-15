"""Production Memory over MemPalace (pinned 3.4.0). Read via searcher.search_memories
(verified). Write: lower-level collection.upsert with the exact metadata schema
the searcher filters on (wing, room keys) — discovered by reading
mempalace.miner._build_drawer_metadata and mempalace.searcher.build_where_filter.

All blocking mempalace calls run in a thread so the asyncio event loop is never
blocked.

Write path (approach B): call palace.get_collection(palace_path, create=True)
which internally uses the ChromaDB backend with hnsw:space=cosine and the
configured embedding function. Model is selected via MEMPALACE_EMBEDDING_MODEL
env var before the first get_collection call. Then upsert the document with the
minimum metadata the searcher needs: wing + room (for scoped filtering).
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from glimpse_brain.memory import MemoryHit

# Virtual "source_file" tag for programmatic drawers so searcher metadata
# fields are populated without an actual file on disk.
_SYNTHETIC_SOURCE = "mempalace_memory://glimpse_brain"


def _safe_wing(customer: str) -> str:
    """Sanitise a customer name into a mempalace wing identifier.

    Wing names must not contain path separators; other special chars are
    collapsed so IDs remain filesystem-safe.
    """
    return re.sub(r"[\\/]+", "_", customer).strip(" ._-") or "unknown"


class MemPalaceMemory:
    """Async Memory backed by a real MemPalace (chromadb + ONNX embedder).

    Parameters
    ----------
    palace_path:
        Directory where the ChromaDB palace lives (created if absent).
    embedding_model:
        ``"minilm"`` (default, fast, English-biased) or ``"embeddinggemma"``
        (multilingual, 300 MB, recommended for production). Stored as intent;
        the actual model is resolved by setting MEMPALACE_EMBEDDING_MODEL
        before the first collection open so the ChromaDB backend picks it up
        via ``mempalace.embedding.get_embedding_function``.
    """

    def __init__(self, palace_path: Path, embedding_model: str = "embeddinggemma") -> None:
        self._palace = str(palace_path)
        self._embedding_model = embedding_model
        Path(palace_path).mkdir(parents=True, exist_ok=True)
        # NOTE: process-global side effect — MemPalace 3.4.0 selects the embedder only via
        # this env var (no per-collection lever).
        os.environ.setdefault("MEMPALACE_EMBEDDING_MODEL", self._embedding_model)

    # ------------------------------------------------------------------
    # Public async interface (Memory Protocol)
    # ------------------------------------------------------------------

    async def recall(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        return await asyncio.to_thread(self._recall_sync, customer, query, k)

    async def write(self, customer: str, content: str, kind: str) -> None:
        await asyncio.to_thread(self._write_sync, customer, content, kind)

    # ------------------------------------------------------------------
    # Synchronous implementations (run in a thread)
    # ------------------------------------------------------------------

    def _recall_sync(self, customer: str, query: str, k: int) -> list[MemoryHit]:
        from mempalace import searcher

        result = searcher.search_memories(
            query,
            palace_path=self._palace,
            wing=_safe_wing(customer),
            n_results=k,
        )
        rows = result.get("results", []) if isinstance(result, dict) else []
        return [
            MemoryHit(
                text=row.get("text", ""),
                kind=str(row.get("room", "")),
                score=float(row.get("similarity", 0.0)),
            )
            for row in rows
        ]

    def _write_sync(self, customer: str, content: str, kind: str) -> None:
        """Store *content* as a drawer scoped to *customer*'s wing and *kind* room.

        Write path: open (or create) the drawers collection via
        ``mempalace.palace.get_collection``, which sets up hnsw:space=cosine
        and passes the embedding function resolved from MEMPALACE_EMBEDDING_MODEL.
        Then upsert the document with the metadata schema the searcher filters on.

        Required metadata keys (discovered from searcher.build_where_filter +
        miner._build_drawer_metadata):
          wing, room           — used by build_where_filter for scoped search
          source_file          — expected by search result formatting (Path().name)
          chunk_index          — int, required by file_already_mined / ID recipe
          filed_at             — ISO timestamp
          normalize_version    — int, required by file_already_mined
          id_recipe            — "v2", mirrors make_drawer_id_from_content
          added_by             — provenance tag
        """
        from mempalace.ids import ID_RECIPE, make_drawer_id_from_content
        from mempalace.palace import NORMALIZE_VERSION, get_collection

        wing = _safe_wing(customer)

        collection = get_collection(self._palace, create=True)

        drawer_id = make_drawer_id_from_content(wing, kind, content)
        # Metadata schema verified against mempalace 3.4.0 (miner._build_drawer_metadata).
        metadata: dict[str, str | int | float] = {
            "wing": wing,
            "room": kind,
            "source_file": _SYNTHETIC_SOURCE,
            "chunk_index": 0,
            "added_by": "glimpse_brain",
            "filed_at": datetime.now(UTC).isoformat(),
            "normalize_version": NORMALIZE_VERSION,
            "id_recipe": ID_RECIPE,
        }
        collection.upsert(
            documents=[content],
            ids=[drawer_id],
            metadatas=[metadata],
        )
