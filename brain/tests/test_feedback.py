from __future__ import annotations

from pathlib import Path

from glimpse_brain.feedback import FeedbackLog, FeedbackRecord
from glimpse_brain.redaction import Redactor


def make_log(tmp_path: Path) -> FeedbackLog:
    # Redact 11-digit phone numbers (same default family as the brain).
    return FeedbackLog(tmp_path / "sub" / "feedback.jsonl", Redactor([r"1[3-9]\d{9}"]))


def record(note: str = "强调赠品", conversation: list[str] | None = None) -> FeedbackRecord:
    return FeedbackRecord(
        ts="2026-06-16T00:00:00Z",
        suggestion_id="s1",
        region_id="region-1",
        verdict="down",
        note=note,
        conversation=conversation if conversation is not None else ["客户: 能便宜点吗"],
        draft="不能便宜",
        customer="老王",
    )


def test_append_then_read_round_trips(tmp_path: Path) -> None:
    log = make_log(tmp_path)  # missing parent dir is created
    log.append(record())
    out = log.read()
    assert len(out) == 1
    assert out[0].note == "强调赠品"
    assert out[0].verdict == "down"
    assert out[0].customer == "老王"


def test_conversation_and_note_redacted_on_write(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    log.append(record(note="打给 13800001111", conversation=["客户: 13800001111 在吗"]))
    out = log.read()
    assert "13800001111" not in out[0].note
    assert "13800001111" not in out[0].conversation[0]


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    log = FeedbackLog(tmp_path / "nope.jsonl", Redactor([]))
    assert log.read() == []
