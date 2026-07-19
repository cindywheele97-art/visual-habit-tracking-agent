from __future__ import annotations

from pathlib import Path

from glimpse_brain.report import build_report
from glimpse_brain.store import BehaviorStore


def seed(tmp_path: Path) -> Path:
    """A miniature 选品 day: explicit run A (clicks+dwells+two outcomes),
    marking-noise click, implicit run B — the smoke-day shape."""
    db = tmp_path / "b.sqlite3"
    s = BehaviorStore(db)
    s.append(kind="selection_control", ts="2026-07-16T09:00:00Z",
             session_id="sel-A", payload={"action": "start"})
    s.append(kind="click", ts="2026-07-16T09:02:10Z", app="com.google.Chrome",
             window_title="连衣裙批发-1688", session_id="sel-A",
             payload={"x": 1, "y": 2, "texts": ["夏季连衣裙 612345678901"], "capture_ok": True})
    s.append(kind="dwell", ts="2026-07-16T09:02:10Z", app="com.google.Chrome",
             window_title="连衣裙批发-1688", session_id="sel-A",
             payload={"start_ts": "2026-07-16T09:02:10Z", "end_ts": "2026-07-16T09:08:40Z",
                      "seconds": 390.0})
    s.append(kind="click", ts="2026-07-16T09:09:00Z", app="com.google.Chrome",
             window_title="雪纺裙工厂-1688", session_id="sel-A",
             payload={"x": 1, "y": 2, "texts": ["雪纺裙 773311224455"], "capture_ok": True})
    s.append(kind="dwell", ts="2026-07-16T09:09:00Z", app="com.google.Chrome",
             window_title="雪纺裙工厂-1688", session_id="sel-A",
             payload={"start_ts": "2026-07-16T09:09:00Z", "end_ts": "2026-07-16T09:19:30Z",
                      "seconds": 630.0})
    s.append(kind="selection_control", ts="2026-07-16T09:28:00Z",
             session_id="sel-A", payload={"action": "end"})
    s.append(kind="click", ts="2026-07-16T09:28:10Z", app="com.google.Chrome",
             window_title="连衣裙批发-1688", session_id="",
             payload={"x": 1, "y": 2, "texts": ["tab focus"], "capture_ok": True})
    s.append(kind="selection_outcome", ts="2026-07-16T09:28:30Z", session_id="sel-A",
             payload={"product_key": "连衣裙批发-1688", "verdict": "selected",
                      "note": "利润空间大"})
    s.append(kind="selection_outcome", ts="2026-07-16T09:29:10Z", session_id="sel-A",
             payload={"product_key": "雪纺裙工厂-1688", "verdict": "shortlisted",
                      "note": "起订量偏高"})
    s.append(kind="click", ts="2026-07-16T10:30:00Z", app="com.google.Chrome",
             window_title="男士T恤清仓-1688", session_id="sel-B",
             payload={"x": 1, "y": 2, "texts": ["纯棉T恤"], "capture_ok": True})
    s.close()
    return db


def test_report_reconstructs_sessions_with_outcomes(tmp_path: Path) -> None:
    # WHY: the dashboard is the flywheel's first consumer — the trajectory →
    # outcome join it renders must reflect the store exactly, or the operator
    # calibrates their 选品 sense against fiction.
    r = build_report(seed(tmp_path))
    assert r["stats"]["sessions"] == 2
    assert r["stats"]["clicks"] == 3          # noise click excluded
    assert r["stats"]["dwell_seconds"] == 1020.0
    assert r["stats"]["outcomes"] == {"selected": 1, "shortlisted": 1, "rejected": 0}
    assert r["noise_clicks"] == 1

    a, b = r["sessions"]                       # chronological
    assert a["session_id"] == "sel-A"
    assert a["explicit"] is True
    assert a["clicks"] == 2 and a["dwell_seconds"] == 1020.0
    assert [o["verdict"] for o in a["outcomes"]] == ["selected", "shortlisted"]
    assert a["outcomes"][0]["note"] == "利润空间大"
    # pages ranked by attention (dwell first)
    assert a["pages"][0]["title"] == "雪纺裙工厂-1688"
    assert a["pages"][0]["dwell_seconds"] == 630.0
    assert b["session_id"] == "sel-B" and b["explicit"] is False
    assert b["outcomes"] == []


def test_report_events_are_chronological_and_typed(tmp_path: Path) -> None:
    r = build_report(seed(tmp_path))
    kinds = [e["kind"] for e in r["sessions"][0]["events"]]
    assert kinds[0] == "selection_control"
    assert kinds[-1] == "selection_outcome"
    ts_list = [e["ts"] for e in r["sessions"][0]["events"]]
    assert ts_list == sorted(ts_list)


def test_report_empty_db_is_well_formed(tmp_path: Path) -> None:
    # WHY (UX floor): the dashboard needs an honest empty state, not a crash,
    # on a fresh install with no browsing recorded yet.
    db = tmp_path / "empty.sqlite3"
    BehaviorStore(db).close()
    r = build_report(db)
    assert r["stats"]["sessions"] == 0
    assert r["sessions"] == []


def test_report_missing_db_raises_cleanly(tmp_path: Path) -> None:
    # WHY: a typo'd --db must fail loud (config fail-loud rule), not render an
    # empty dashboard that reads as "no data yet".
    import pytest

    with pytest.raises(FileNotFoundError):
        build_report(tmp_path / "nope.sqlite3")
