import Foundation
import Testing
@testable import GlimpseShellLib

// WHY: dwell is the primary implicit-attention signal of 选品 — these tests pin
// the interval semantics (close on focus change / idle, filter noise) that the
// analytics layer will treat as ground truth.

@Test
func focusSwitchClosesIntervalWithCorrectSeconds() {
    let t = DwellTracker(minSeconds: 2)
    #expect(t.focusChanged(app: "a", title: "商品页A", url: "u1", at: 100) == nil)
    let closed = t.focusChanged(app: "b", title: "商品页B", url: "u2", at: 145)
    #expect(closed == DwellInterval(app: "a", title: "商品页A", url: "u1", start: 100, end: 145))
    #expect(closed?.seconds == 45)
}

@Test
func sameContextPollIsANoOp() {
    // The sensor polls every ~2s; unchanged focus must not close/reopen.
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "同一页", url: "u", at: 100)
    #expect(t.focusChanged(app: "a", title: "同一页", url: "u", at: 102) == nil)
    let closed = t.focusChanged(app: nil, title: "", url: "", at: 130)
    #expect(closed?.seconds == 30)  // one continuous interval, not fragments
}

@Test
func intervalsBelowMinimumAreNoise() {
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "x", url: "", at: 100)
    #expect(t.focusChanged(app: "b", title: "y", url: "", at: 101) == nil)  // 1s flick-through
}

@Test
func idleClosesAtLastActivityAndReturnReopens() {
    // WHY: walking away must not count as attention — the interval ends at the
    // LAST INPUT moment, not when the idle threshold fired.
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "页", url: "", at: 100)
    let closed = t.idled(at: 160)  // caller passes last-activity time
    #expect(closed?.end == 160 && closed?.seconds == 60)
    #expect(t.idled(at: 200) == nil)  // already closed — idempotent
    // User comes back: the next poll reopens a fresh interval.
    #expect(t.focusChanged(app: "a", title: "页", url: "", at: 300) == nil)
    #expect(t.focusChanged(app: nil, title: "", url: "", at: 310)?.start == 300)
}

@Test
func nonTrackedFocusClosesWithoutOpening() {
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "页", url: "", at: 100)
    _ = t.focusChanged(app: nil, title: "", url: "", at: 110)
    #expect(t.focusChanged(app: nil, title: "", url: "", at: 120) == nil)  // nothing open
}
