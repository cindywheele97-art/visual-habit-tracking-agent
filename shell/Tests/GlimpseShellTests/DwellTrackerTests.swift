import Foundation
import Testing
@testable import GlimpseShellLib

// WHY: dwell is the primary implicit-attention signal of 选品. On the native
// tier we can only bound attention by APP focus + input idle — per-page dwell
// needs the P7.4 browser extension (Page Visibility). These tests pin the
// coarse-but-honest native semantics: intervals split on APP change only, the
// window title rides along as descriptive metadata (so title flicker — 旺旺
// notification counters, blinking chat titles — never fragments an interval),
// and a hands-off close never records a negative/zero-start interval.

@Test
func appSwitchClosesIntervalWithLastTitle() {
    let t = DwellTracker(minSeconds: 2)
    #expect(t.focusChanged(app: "a", title: "商品A", at: 100) == nil)
    let closed = t.focusChanged(app: "b", title: "商品B", at: 145)
    #expect(closed == DwellInterval(app: "a", title: "商品A", start: 100, end: 145))
    #expect(closed?.seconds == 45)
}

@Test
func titleFlickerWithinSameAppNeverFragments() {
    // WHY (audit finding): kCGWindowName mutates in place — "(3) 旺旺" → "(5) 旺旺",
    // blinking chat titles. Same app = one continuous interval; the title
    // recorded is the LAST one observed.
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "旺旺", at: 100)
    #expect(t.focusChanged(app: "a", title: "(3) 旺旺", at: 102) == nil)
    #expect(t.focusChanged(app: "a", title: "(5) 旺旺", at: 104) == nil)
    let closed = t.focusChanged(app: nil, title: "", at: 130)
    #expect(closed?.seconds == 30)           // one interval, not three
    #expect(closed?.title == "(5) 旺旺")       // last observed title
}

@Test
func intervalsBelowMinimumAreNoise() {
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "x", at: 100)
    #expect(t.focusChanged(app: "b", title: "y", at: 101) == nil)  // 1s flick-through
}

@Test
func idleCloseIsClampedNeverNegative() {
    // WHY (audit finding): intervals open at tick time (T) but idle closes at
    // last-input time, which can precede T when the user goes hands-off right
    // after the focus tick. The close must clamp to >= start, never emit a
    // negative/zero-length interval that reads as corrupt data.
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "页", at: 100)
    // last input was at 98 (before the 100 tick) → naive end 98 < start 100.
    #expect(t.idled(at: 98) == nil)            // clamped to zero-length → filtered, not negative
    // A genuine hands-on interval still closes correctly.
    _ = t.focusChanged(app: "a", title: "页", at: 200)
    let closed = t.idled(at: 260)
    #expect(closed?.end == 260 && closed?.seconds == 60)
    #expect(t.idled(at: 300) == nil)           // already closed — idempotent
}

@Test
func idleThenReturnReopensFreshInterval() {
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "页", at: 100)
    _ = t.idled(at: 160)
    #expect(t.focusChanged(app: "a", title: "页", at: 300) == nil)  // reopened
    #expect(t.focusChanged(app: nil, title: "", at: 330)?.start == 300)
}

@Test
func leavingTrackedAppsClosesWithoutReopening() {
    let t = DwellTracker(minSeconds: 2)
    _ = t.focusChanged(app: "a", title: "页", at: 100)
    _ = t.focusChanged(app: nil, title: "", at: 130)
    #expect(t.focusChanged(app: nil, title: "", at: 140) == nil)  // nothing open
}
