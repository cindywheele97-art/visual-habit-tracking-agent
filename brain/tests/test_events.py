from __future__ import annotations

import json
from pathlib import Path

from glimpse_brain.events import EventLog
from glimpse_brain.redaction import Redactor


def test_append_writes_envelope_and_creates_parents(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "deep" / "events.jsonl", Redactor([]))
    log.append("observation", "region-1", {"inbound": ["你好"]})
    lines = (tmp_path / "deep" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["kind"] == "observation"
    assert rec["region_id"] == "region-1"
    assert rec["payload"] == {"inbound": ["你好"]}
    assert "ts" in rec


def test_append_only_accumulates(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "e.jsonl", Redactor([]))
    log.append("a", "r", {})
    log.append("b", "r", {})
    assert len((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_payload_is_redacted_before_write(tmp_path: Path) -> None:
    # The event log is a privacy boundary too — same rules as the LLM path.
    log = EventLog(tmp_path / "e.jsonl", Redactor([r"1[3-9]\d{9}"]))
    log.append("observation", "r", {"inbound": ["电话13812345678"]})
    assert "13812345678" not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")
