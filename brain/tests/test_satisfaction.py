from __future__ import annotations

from glimpse_brain.satisfaction import SatisfactionTracker


def make(window: int = 5, threshold: float = 1.0, min_ratings: int = 3) -> SatisfactionTracker:
    return SatisfactionTracker(window=window, threshold=threshold, min_ratings=min_ratings)


def test_below_min_ratings_never_ready() -> None:
    # 100% rate but too few samples → no advice off thin evidence.
    t = make(min_ratings=3)
    assert t.record("up") is False
    assert t.record("up") is False
    assert t.ready() is False


def test_rising_edge_fires_once_then_is_quiet() -> None:
    # Crossing into ready fires once; further 👍s do not nag.
    t = make(min_ratings=3, threshold=1.0)
    t.record("up")
    t.record("up")
    assert t.record("up") is True   # rising edge
    assert t.record("up") is False  # still ready → quiet


def test_drop_then_rise_refires() -> None:
    t = make(window=5, min_ratings=3, threshold=0.8)
    for _ in range(3):
        t.record("up")
    assert t.record("up") is True          # ready, fired
    assert t.record("down") is False       # 4up/1down=0.8 still ready, quiet
    t.record("down")                       # rate drops below 0.8 → re-arm
    assert t.ready() is False
    for _ in range(5):
        t.record("up")                     # window refills with up
    assert t._advised is True              # fired again on the new rise


def test_window_ages_out_old_verdicts() -> None:
    # A 👎 outside the window no longer counts against the rate.
    t = make(window=3, min_ratings=3, threshold=1.0)
    t.record("down")
    t.record("up")
    t.record("up")
    t.record("up")  # evicts the initial down; window is now up,up,up
    assert t.rate == 1.0
    assert t.ready() is True


def test_seed_sets_advised_to_ready() -> None:
    # Relaunch while already qualified must not re-pop the advisory.
    t = make(min_ratings=3, threshold=1.0)
    t.seed(["up", "up", "up"])
    assert t.ready() is True
    assert t.record("up") is False  # already advised from seed → no re-fire
