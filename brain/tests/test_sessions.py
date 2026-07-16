from __future__ import annotations

from glimpse_brain.sessions import Sessionizer


def test_first_event_opens_a_session() -> None:
    # WHY: every behavior event must carry a session_id — the flywheel's atomic
    # record is the trajectory (audit §三 B3), not the isolated click.
    s = Sessionizer(idle_gap_seconds=900)
    sid = s.observe(1000.0)
    assert sid != ""
    assert s.observe(1100.0) == sid  # within the idle gap → same trajectory


def test_idle_gap_starts_a_new_session() -> None:
    # WHY: a long inactivity gap means a different 选品 run; automatic
    # segmentation by gap is pure code, no LLM (Rule 5).
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    still_first = s.observe(1899.0)  # 899s gap < 900 → same
    assert still_first == first
    second = s.observe(2800.0)  # 901s gap > 900 → new
    assert second != first


def test_explicit_begin_forces_a_fresh_boundary() -> None:
    # WHY: '开始选品' is ground-truth the operator gives — it must start a new
    # session even with no idle gap (two runs back-to-back).
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    second = s.begin(1010.0)  # explicit, only 10s later
    assert second != first
    assert s.observe(1020.0) == second


def test_end_then_observe_opens_a_new_session() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    s.end()
    assert s.current() == ""  # closed
    second = s.observe(1010.0)  # fresh, even within the gap
    assert second != first


def test_current_reflects_open_session() -> None:
    # WHY: a selection outcome attaches to the ACTIVE session without extending
    # it — current() is that key, "" when none is open.
    s = Sessionizer()
    assert s.current() == ""
    sid = s.observe(1000.0)
    assert s.current() == sid
    s.end()
    assert s.current() == ""


def test_session_ids_are_unique_and_deterministic() -> None:
    s = Sessionizer(idle_gap_seconds=1)
    a = s.observe(1000.0)
    b = s.observe(2000.0)  # gap > 1 → new
    assert a != b
    # deterministic: same start ts → same id (replayable audit trail)
    assert Sessionizer().observe(1000.0) == a
