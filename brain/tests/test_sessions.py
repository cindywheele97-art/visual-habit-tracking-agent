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
    # deterministic: a fresh instance's first session at the same ts matches
    # (replayable audit trail).
    assert Sessionizer().observe(1000.0) == a


def test_two_sessions_in_the_same_second_get_distinct_ids() -> None:
    # WHY (review finding): whole-second shell timestamps make ts*1000 collide;
    # a casual click then a deliberate 开始选品 in the same second must NOT merge
    # into one id, or two runs silently become one.
    s = Sessionizer(idle_gap_seconds=900)
    a = s.observe(1000.0)     # opens run 1
    b = s.begin(1000.0)       # explicit new run, same second
    assert a != b


def test_outcome_after_end_binds_to_the_just_closed_run() -> None:
    # WHY (review finding, high): the natural flow 结束选品 → 标记淘汰 must bind
    # the outcome (the flywheel's TARGET variable) to the run just finished, not
    # to "" — an empty session_id is permanently unjoinable.
    s = Sessionizer()
    run = s.begin(1000.0)
    s.observe(1010.0)         # a click in the run
    s.end()
    assert s.current() == ""                    # run is closed
    assert s.last_session(at=1015.0) == run     # …but a prompt verdict finds it
    # A second run then an end → last_session tracks the most recent.
    run2 = s.begin(5000.0)
    s.end()
    assert s.last_session(at=5005.0) == run2


def test_outcome_long_after_the_last_run_is_sessionless() -> None:
    # WHY (re-review finding): _last_id must not live forever — a verdict marked
    # hours later, in a fresh context with no run, would silently mislabel a
    # long-dead trajectory. Beyond the idle gap the honest answer is "" (same
    # segmentation semantics as clicks: within the gap it would have merged).
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.observe(1010.0)
    s.end()
    assert s.last_session(at=1900.0) == run   # 890s after last activity: binds
    assert s.last_session(at=2000.0) == ""    # 990s: a different context now
    # An OPEN run binds regardless of elapsed time — only closed runs expire.
    run2 = s.begin(10_000.0)
    assert s.last_session(at=99_999.0) == run2


def test_outcome_before_any_run_is_sessionless() -> None:
    s = Sessionizer()
    assert s.last_session(at=1000.0) == ""  # truly no run → "" (honest)


def test_future_skew_self_corrects_on_the_next_active_event() -> None:
    # WHY (re-review finding, high): one future-skewed timestamp (e.g. the
    # _epoch time.time() fallback during a spool replay) must NOT permanently
    # freeze idle-gap splitting. Active events are the recency ground truth and
    # reset the clock unconditionally — damage is bounded to at most one
    # boundary decision, after which later real gaps split runs again.
    s = Sessionizer(idle_gap_seconds=900)
    r1 = s.observe(32_400.0)                        # 09:00, run R1
    s.observe(61_200.0, opens=False)                # malformed dwell → 17:00 skew
    assert s.observe(39_600.0) == r1                # 11:00: one merged decision (bounded)
    r3 = s.observe(46_800.0)                        # 13:00: 2h after 11:00 → MUST split
    assert r3 != r1


def test_passive_dwell_never_rewinds_the_clock_or_splits() -> None:
    # WHY (review finding, high): a DwellMsg closes AFTER the clicks in its span
    # but was stamped by its START. A backdated passive event must not rewind
    # the recency clock and spuriously split one continuous run.
    s = Sessionizer(idle_gap_seconds=900)
    run = s.observe(1000.0)
    s.observe(1600.0)                       # click, same run, clock at 1600
    # a dwell that STARTED at 1000 but closes now: passive, backdated
    assert s.observe(1000.0, opens=False) == run   # attaches, no new run
    # next click 800s after the real last activity (1600) → still same run;
    # would have split if the dwell had rewound the clock to 1000.
    assert s.observe(2400.0) == run


def test_passive_dwell_does_not_open_or_reopen_a_run() -> None:
    # WHY: a dwell is passive evidence — with no open run (e.g. right after
    # 结束选品) it must attach to the last run, never fabricate a phantom one.
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.end()
    assert s.observe(1005.0, opens=False) == run   # trailing dwell → last run
    assert s.current() == ""                        # still closed, no phantom open
    # a dwell before any run at all → sessionless, still no fabricated run
    fresh = Sessionizer()
    assert fresh.observe(500.0, opens=False) == ""
    assert fresh.current() == ""
