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


class EventLog:
    def __init__(
        self,
        path: Path,
        redactor: Redactor,
        max_bytes: int = DEFAULT_EVENT_LOG_MAX_BYTES,
    ) -> None:
        self._path = path
        self._redactor = redactor
        self._max_bytes = max_bytes
        path.parent.mkdir(parents=True, exist_ok=True)

    def _file_size(self) -> int:
        try:
            return self._path.stat().st_size
        except FileNotFoundError:
            return 0

    def _maybe_rotate(self) -> None:
        if self._file_size() <= self._max_bytes:
            return
        os.replace(self._path, self._path.with_suffix(".jsonl.1"))

    def append(self, kind: str, region_id: str, payload: dict[str, object]) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "region_id": region_id,
            "payload": self._redactor.redact_payload(payload),
        }
        try:
            self._maybe_rotate()
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())  # crash during a long mission must not lose the trail
        except OSError as exc:
            log.warning("event log append failed (%s): %s", self._path, exc)
