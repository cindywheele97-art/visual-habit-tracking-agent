from __future__ import annotations

from glimpse_brain.sessions import Sessionizer

# WHY (module-level): session_id is the flywheel's atomic join key and the
# outcome its target variable — silent mislabeling here is permanent, invisible
# corruption. These tests pin the semantics four adversarial review rounds
# converged on:
#   clicks  = active ground truth (FIFO order)   → open runs, ASSIGN the clock
#   dwells  = passive, systematically late        → observe_span: continuation
#             labels, detached is honestly "",    never opens/extends grace
#   begin/end = operator ground truth             → end anchors the grace window
#             and outranks implicit stray runs for verdict binding


def test_first_event_opens_a_session() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    sid = s.observe(1000.0)
    assert sid != ""
    assert s.observe(1100.0) == sid  # within the idle gap → same trajectory


def test_idle_gap_starts_a_new_session() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    assert s.observe(1899.0) == first   # 899s < 900 → same
    assert s.observe(2800.0) != first   # 901s > 900 → new


def test_explicit_begin_forces_a_fresh_boundary() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    second = s.begin(1010.0)  # explicit, only 10s later
    assert second != first
    assert s.observe(1020.0) == second


def test_end_then_observe_opens_a_new_session() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    first = s.observe(1000.0)
    s.end(1005.0)
    assert s.current() == ""
    assert s.observe(1010.0) != first  # fresh, even within the gap


def test_current_reflects_open_session() -> None:
    s = Sessionizer()
    assert s.current() == ""
    sid = s.observe(1000.0)
    assert s.current() == sid
    s.end(1001.0)
    assert s.current() == ""


def test_session_ids_are_unique_and_deterministic() -> None:
    s = Sessionizer(idle_gap_seconds=1)
    a = s.observe(1000.0)
    b = s.observe(2000.0)
    assert a != b
    assert Sessionizer().observe(1000.0) == a  # replayable first id


def test_two_sessions_in_the_same_second_get_distinct_ids() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    a = s.observe(1000.0)
    b = s.begin(1000.0)
    assert a != b


def test_future_skew_self_corrects_on_the_next_active_event() -> None:
    # WHY: one future-skewed ts (_epoch wall-clock fallback during replay) must
    # corrupt at most ONE boundary decision — active events reassign the clock.
    s = Sessionizer(idle_gap_seconds=900)
    r1 = s.observe(32_400.0)                             # 09:00, run R1
    s.observe_span(32_500.0, 61_200.0)                   # skewed dwell end → 17:00
    assert s.observe(39_600.0) == r1                     # 11:00: ≤1 merged decision
    assert s.observe(46_800.0) != r1                     # 13:00: 2h gap → MUST split


# ---- dwell (passive span) semantics ----------------------------------------


def test_late_overlapping_dwell_attaches_and_extends_open_run() -> None:
    # WHY (round-1 finding): a dwell closes AFTER the clicks in its span; it
    # must attach without rewinding the clock, and — as proof of continuous
    # attention on an OPEN run — extend recency to its end.
    s = Sessionizer(idle_gap_seconds=900)
    run = s.observe(1000.0)
    s.observe(1600.0)
    assert s.observe_span(1000.0, 1400.0) == run  # backdated, no rewind…
    assert s.observe(2400.0) == run               # …clock still ≥1600 (800s gap)
    assert s.observe_span(2400.0, 3900.0) == run  # reading till 3900, run open
    assert s.observe(4700.0) == run               # 800s after dwell end → continuous


def test_detached_dwell_is_unattributed_and_inert() -> None:
    # WHY (round-3 finding, high): a scroll-only dwell HOURS later must not
    # resurrect a dead run — no label, no clock effect; the next click still
    # opens a fresh run. (Scroll-only trajectories stay coarse until P7.4.)
    s = Sessionizer(idle_gap_seconds=900)
    r1 = s.observe(1000.0)
    s.observe(1300.0)
    assert s.observe_span(11_800.0, 12_100.0) == ""  # 3h later: unattributed
    assert s.observe(12_200.0) != r1                 # split preserved
    # before any run at all → equally inert
    fresh = Sessionizer()
    assert fresh.observe_span(500.0, 600.0) == ""
    assert fresh.current() == ""


def test_trailing_dwell_labels_closed_run_without_extending_grace() -> None:
    # WHY (round-3 finding): attention that continues right past 结束选品 belongs
    # to the run — but a CLOSED run's grace window must stay anchored at the
    # close; ambient dwells must not keep it bindable.
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.observe(1010.0)
    s.end(1015.0)
    assert s.observe_span(1015.0, 1060.0) == run     # trailing dwell: labeled
    assert s.last_session(at=1900.0) == run          # within grace of the close
    # 1950 is inside the gap of the DWELL's end (1060+900=1960) but outside the
    # gap of the CLOSE (1015+900=1915): only an illegally advanced clock binds.
    assert s.last_session(at=1950.0) == ""
    assert s.last_session(at=2000.0) == ""           # dwell did NOT extend it


def test_ambient_dwells_cannot_keep_a_dead_run_bindable() -> None:
    # WHY (round-3 finding): app-switch dwells arrive all day; a chain of them
    # must not extend a closed run's bindability indefinitely.
    s = Sessionizer(idle_gap_seconds=900)
    s.begin(1000.0)
    s.observe(1010.0)
    s.end(1015.0)
    for i in range(10):  # dwells every 600s for ~100 min (last ends 7060)
        start = 1600.0 + i * 600.0
        s.observe_span(start, start + 60.0)
    # 7500 is within the gap of the LAST dwell's end (7060+900) but hours past
    # the close (1015): a chain-extended clock would still bind here.
    assert s.last_session(at=7500.0) == ""
    assert s.last_session(at=8000.0) == ""  # ~2h later: honestly sessionless
    assert s.current() == ""                # and nothing got reopened


# ---- verdict (outcome) binding ----------------------------------------------


def test_outcome_after_end_binds_to_the_just_closed_run() -> None:
    s = Sessionizer()
    run = s.begin(1000.0)
    s.observe(1010.0)
    s.end(1015.0)
    assert s.current() == ""
    assert s.last_session(at=1020.0) == run
    run2 = s.begin(5000.0)
    s.end(5002.0)
    assert s.last_session(at=5005.0) == run2


def test_end_anchors_the_grace_window_not_the_last_click() -> None:
    # WHY (round-3 finding): scroll-reading emits no clicks; 结束选品 16 min
    # after the last click, then 标记 10s later, must still bind — the explicit
    # close is operator ground truth for "the run existed until now".
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.observe(1010.0)          # last click
    s.end(1970.0)              # 16 min of click-free reading, then explicit end
    assert s.last_session(at=1980.0) == run


def test_stray_click_after_end_does_not_steal_the_verdict() -> None:
    # WHY (round-3 finding, high): marking REQUIRES clicking the product tab
    # frontmost first (product_key = front window title), which opens an
    # implicit run — the verdict must still bind the explicitly ended run.
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.observe(1010.0)
    s.end(1200.0)
    phantom = s.observe(1215.0)             # the tab-focus click
    assert phantom != run                    # the click itself is new browsing…
    assert s.last_session(at=1220.0) == run  # …but the verdict binds the closed run
    # batch marking: another tab click + mark still binds the ended run
    s.observe(1230.0)
    assert s.last_session(at=1235.0) == run


def test_begin_clears_the_ended_preference() -> None:
    # WHY: after an explicit 开始选品, verdicts belong to the NEW run.
    s = Sessionizer(idle_gap_seconds=900)
    r1 = s.begin(1000.0)
    s.end(1100.0)
    r2 = s.begin(1110.0)
    assert s.last_session(at=1120.0) == r2
    assert r2 != r1


def test_outcome_long_after_the_last_run_is_sessionless() -> None:
    s = Sessionizer(idle_gap_seconds=900)
    run = s.begin(1000.0)
    s.observe(1010.0)
    s.end(1015.0)
    assert s.last_session(at=1900.0) == run   # within grace of the close
    assert s.last_session(at=2000.0) == ""    # beyond: a different context
    run2 = s.begin(10_000.0)
    assert s.last_session(at=99_999.0) == run2  # OPEN runs never expire


def test_outcome_before_any_run_is_sessionless() -> None:
    assert Sessionizer().last_session(at=1000.0) == ""


# ---- restart rehydration ------------------------------------------------------


def test_rehydrate_restores_binding_and_open_run() -> None:
    # WHY (round-3 finding): the sessionizer is memory-only; a brain restart
    # must not orphan a prompt verdict nor split an in-flight run — state is
    # reconstructible from the store and rehydrate() restores it.
    s = Sessionizer(idle_gap_seconds=900)
    s.rehydrate(session_id="sel-1-0", last_ts=1000.0, open_run=True, ended_ts=None)
    assert s.current() == "sel-1-0"
    assert s.observe(1100.0) == "sel-1-0"       # mid-run click: same run, no split
    s2 = Sessionizer(idle_gap_seconds=900)
    s2.rehydrate(session_id="sel-2-0", last_ts=1000.0, open_run=False, ended_ts=1005.0)
    assert s2.current() == ""
    assert s2.last_session(at=1050.0) == "sel-2-0"   # prompt verdict still binds
    assert s2.last_session(at=2000.0) == ""          # grace still expires
