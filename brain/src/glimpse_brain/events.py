"""Append-only JSONL event log — the substrate later phases read."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from glimpse_brain.redaction import Redactor


class EventLog:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self._path = path
        self._redactor = redactor
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, region_id: str, payload: dict[str, object]) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "kind": kind,
            "region_id": region_id,
            "payload": self._redactor.redact_payload(payload),
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
