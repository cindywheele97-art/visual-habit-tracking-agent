"""Append-only JSONL event log — the substrate later phases read.

append never raises — 审计尾迹的丢失优于杀死整条管线
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from glimpse_brain.redaction import Redactor

log = logging.getLogger("glimpse.events")

DEFAULT_EVENT_LOG_MAX_BYTES = 50 * 1024 * 1024

# Envelope dialect marker: longitudinal data outlives refactors, so every row
# says which shape it was written in.
ENVELOPE_VERSION = 1


class EventLog:
    def __init__(
        self,
        path: Path,
        redactor: Redactor,
        max_bytes: int = DEFAULT_EVENT_LOG_MAX_BYTES,
        raw_kinds: frozenset[str] = frozenset(),
    ) -> None:
        self._path = path
        self._redactor = redactor
        self._max_bytes = max_bytes
        # Per-kind redaction policy seam (audit §十): habit-event kinds pass
        # through unredacted — their digit runs ARE the flywheel join keys and
        # the data never leaves this machine. Everything else stays masked.
        self._raw_kinds = raw_kinds
        path.parent.mkdir(parents=True, exist_ok=True)

    def _file_size(self) -> int:
        try:
            return self._path.stat().st_size
        except FileNotFoundError:
            return 0

    def _archive_path(self) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        candidate = self._path.with_name(f"{self._path.stem}-{stamp}{self._path.suffix}")
        n = 1
        while candidate.exists():  # several rotations within one second
            candidate = self._path.with_name(
                f"{self._path.stem}-{stamp}-{n}{self._path.suffix}"
            )
            n += 1
        return candidate

    def _maybe_rotate(self) -> None:
        if self._file_size() <= self._max_bytes:
            return
        # ARCHIVE, never overwrite (audit §三 S1): the flywheel is longitudinal —
        # CTR/GMV outcomes arrive months after the behavior they explain.
        # Dated, collision-safe generations; discard is an owner decision, not
        # a side effect.
        os.replace(self._path, self._archive_path())

    def append(self, kind: str, region_id: str, payload: dict[str, object]) -> None:
        record = {
            "v": ENVELOPE_VERSION,
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "region_id": region_id,
            "payload": payload
            if kind in self._raw_kinds
            else self._redactor.redact_payload(payload),
        }
        try:
            self._maybe_rotate()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())  # crash during a long mission must not lose the trail
        except OSError as exc:
            log.warning("event log append failed (%s): %s", self._path, exc)
