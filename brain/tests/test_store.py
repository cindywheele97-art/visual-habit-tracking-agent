from __future__ import annotations

# (latest_session_state tests appended below — rehydration source for P7.3.)

import json
import logging
from pathlib import Path

import pytest

from glimpse_brain.store import BehaviorStore


def test_roundtrip_insert_and_query(tmp_path: Path) -> None:
    # WHY (audit §三 S3): the flywheel needs cohort/funnel/join queries with
    # predicate pushdown — typed columns must survive a write/read cycle.
    store = BehaviorStore(tmp_path / "behavior.sqlite3")
    store.append(
        kind="click",
        ts="2026-07-16T10:00:00+00:00",
        app="com.google.Chrome",
        window_title="夏季连衣裙 - 1688",
        url="https://detail.1688.com/offer/612345678901.html",
        payload={"x": 1.0, "y": 2.0, "texts": ["连衣裙", "¥99"]},
    )
    rows = store.query(kind="click")
    assert len(rows) == 1
    row = rows[0]
    assert row["app"] == "com.google.Chrome"
    assert row["url"].endswith("612345678901.html")
    assert row["window_title"] == "夏季连衣裙 - 1688"
    assert json.loads(row["payload"])["texts"] == ["连衣裙", "¥99"]
    assert row["schema_version"] == 1
    store.close()


def test_query_filters_by_kind(tmp_path: Path) -> None:
    store = BehaviorStore(tmp_path / "b.sqlite3")
    store.append(kind="click", ts="t1")
    store.append(kind="dwell", ts="t2")
    assert len(store.query(kind="dwell")) == 1
    assert len(store.query()) == 2
    store.close()


def test_append_is_fail_soft_but_loud(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # WHY: same contract as EventLog.append — an analytics write must never
    # kill the dispatch loop, but the failure must be visible in logs.
    with caplog.at_level(logging.WARNING):
        store = BehaviorStore(tmp_path)  # path is a directory → cannot open
        store.append(kind="click", ts="t")
    assert "behavior store" in caplog.text
    assert store.query() == []  # degraded, not crashed


def test_session_state_roundtrip_and_fail_soft(tmp_path: Path) -> None:
    # WHY (merge gate, all 6 findings): rehydrating from event rows is a lossy
    # projection — the store must persist the Sessionizer's VERBATIM snapshot.
    # Same fail-soft contract as append: never raises, loud in logs, None when
    # absent/corrupt.
    from glimpse_brain.store import BehaviorStore

    store = BehaviorStore(tmp_path / "b.sqlite3")
    assert store.load_session_state() is None  # empty store

    state = {
        "current": "sel-2-1",
        "last_id": "sel-2-1",
        "last_ts": 1215.0,
        "ended_id": "sel-1-0",
        "ended_ts": 1200.0,
        "seq": 2,
    }
    store.save_session_state(state)
    assert store.load_session_state() == state

    # overwrite (single-row semantics), including clearing the ended anchor
    state2 = dict(state, current=None, ended_id=None, ended_ts=None, seq=3)
    store.save_session_state(state2)
    assert store.load_session_state() == state2
    store.close()

    # corrupt payload → None + warning, never an exception
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "b.sqlite3"))
    conn.execute("UPDATE session_state SET state = 'not json'")
    conn.commit()
    conn.close()
    store2 = BehaviorStore(tmp_path / "b.sqlite3")
    assert store2.load_session_state() is None
    store2.close()
