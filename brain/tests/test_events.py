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


def test_append_still_writes_redacted(tmp_path: Path) -> None:
    # The event log is a privacy boundary too — same rules as the LLM path.
    log = EventLog(tmp_path / "e.jsonl", Redactor([r"1[3-9]\d{9}"]))
    log.append("observation", "r", {"inbound": ["电话13812345678"]})
    assert "13812345678" not in (tmp_path / "e.jsonl").read_text(encoding="utf-8")


def test_append_survives_unwritable_path(tmp_path: Path) -> None:
    # OSError on append must not propagate into _dispatch and kill the shell connection.
    log = EventLog(tmp_path, Redactor([]))  # path is a directory → open() raises OSError
    log.append("observation", "r", {"x": 1})


def test_rotation_archives_generations_never_deletes(tmp_path: Path) -> None:
    # WHY (audit §三 S1): the flywheel is longitudinal — CTR/GMV outcomes arrive
    # months after the behavior. Rotation must ARCHIVE history (dated files,
    # collision-safe), never overwrite a previous generation.
    path = tmp_path / "events.jsonl"
    log = EventLog(path, Redactor([]), max_bytes=200)
    for i in range(60):
        log.append("observation", "r", {"n": i, "pad": "x" * 40})
    archives = sorted(tmp_path.glob("events-*.jsonl"))
    assert len(archives) >= 2  # several rotations happened, all generations kept
    total_lines = sum(
        len(p.read_text(encoding="utf-8").splitlines()) for p in [*archives, path]
    )
    assert total_lines == 60  # nothing was destroyed


def test_raw_kinds_bypass_redaction_but_others_do_not(tmp_path: Path) -> None:
    # WHY (privacy freeze, audit §十): habit events keep entity IDs — the flywheel
    # join keys — in plaintext (local-only store); CS conversation events keep
    # full redaction. One log, per-kind policy.
    log = EventLog(
        tmp_path / "e.jsonl",
        Redactor([r"\d{10,}"]),
        raw_kinds=frozenset({"click"}),
    )
    log.append("click", "", {"texts": ["商品6212345678901234"]})
    log.append("observation", "r", {"inbound": ["订单6212345678901234"]})
    lines = (tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()
    assert "6212345678901234" in lines[0]  # habit event: raw join key preserved
    assert "6212345678901234" not in lines[1]  # CS event: still masked


def test_envelope_carries_schema_version(tmp_path: Path) -> None:
    # WHY: longitudinal data outlives refactors; rows must say what dialect they are.
    import json

    log = EventLog(tmp_path / "e.jsonl", Redactor([]))
    log.append("click", "", {})
    rec = json.loads((tmp_path / "e.jsonl").read_text(encoding="utf-8"))
    assert rec["v"] == 1


def test_rotation_caps_live_file_size(tmp_path: Path) -> None:
    # The LIVE file stays bounded (rotation trigger works); history moves to
    # dated archives instead of being overwritten.
    path = tmp_path / "events.jsonl"
    log = EventLog(path, Redactor([]), max_bytes=200)
    for i in range(20):
        log.append("observation", "r", {"n": i, "pad": "x" * 40})
    assert path.stat().st_size < 400  # cap + at most one record of slack
    assert len(list(tmp_path.glob("events-*.jsonl"))) >= 1
    log.append("after-rotate", "r", {"marker": True})
    assert any(
        "after-rotate" in line
        for line in path.read_text(encoding="utf-8").splitlines()
    )
