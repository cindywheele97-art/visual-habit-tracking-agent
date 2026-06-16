"""Durable feedback corpus: the raw material the offline distiller reads. Every
field that can carry PII is redacted on write (customer is the memory join key,
already stored raw by the memory subsystem, so it is kept as-is here)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from glimpse_brain.redaction import Redactor

log = logging.getLogger("glimpse.feedback")


@dataclass(frozen=True)
class FeedbackRecord:
    ts: str
    suggestion_id: str
    region_id: str
    verdict: str
    note: str
    conversation: list[str]
    draft: str
    customer: str


class FeedbackLog:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self._path = path
        self._redactor = redactor
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: FeedbackRecord) -> None:
        redacted = FeedbackRecord(
            ts=record.ts,
            suggestion_id=record.suggestion_id,
            region_id=record.region_id,
            verdict=record.verdict,
            note=self._redactor.redact(record.note),
            conversation=[self._redactor.redact(line) for line in record.conversation],
            draft=self._redactor.redact(record.draft),
            customer=record.customer,
        )
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(redacted), ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError:  # a disk failure must not break the feedback path
            log.warning("feedback corpus append failed", exc_info=True)

    def read(self) -> list[FeedbackRecord]:
        if not self._path.exists():
            return []
        records: list[FeedbackRecord] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(FeedbackRecord(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # skip a corrupt line, keep the rest
        return records
