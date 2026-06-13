import CoreGraphics
import Testing
@testable import GlimpseShellLib

private final class MockSyntheticInput: SyntheticInput {
    var clicks = 0
    var pastes = 0
    var returns = 0
    func click(at point: CGPoint) { clicks += 1 }
    func paste() { pastes += 1 }
    func pressReturn() { returns += 1 }
}

/// Builds a Sender with controllable dependencies and captures its outputs.
private final class Harness {
    let mock = MockSyntheticInput()
    var frontmostAllowed = true
    var capturedCountdown: Countdown?
    var replied: [(String, String)] = []
    var refusals: [RefuseReason] = []
    var pasteboard = ""
    lazy var sender = Sender(
        synthetic: mock,
        isFrontmostAllowed: { self.frontmostAllowed },
        inputBoxPoint: { CGPoint(x: 100, y: 200) },
        accessibilityTrusted: { true },
        setPasteboard: { self.pasteboard = $0 },
        presentRefusal: { self.refusals.append($0) },
        presentCountdown: { self.capturedCountdown = $0 },
        emitReplied: { id, mode in self.replied.append((id, mode)) }
    )
}

@Test
func fillPlanPastesAndNeverPressesReturn() {
    let h = Harness()
    h.sender.handle(suggestionId: "s1", text: "你好", autoSendOn: false, stale: false)
    #expect(h.mock.clicks == 1)
    #expect(h.mock.pastes == 1)
    #expect(h.mock.returns == 0)
    #expect(h.pasteboard == "你好")
    #expect(h.replied.count == 1 && h.replied[0] == ("s1", "fill"))
}

@Test
func autoSendPressesReturnOnceWhenCountdownCompletesAndAppStillFrontmost() throws {
    let h = Harness()
    h.sender.handle(suggestionId: "s2", text: "好的", autoSendOn: true, stale: false)
    #expect(h.mock.pastes == 1)        // filled immediately
    #expect(h.mock.returns == 0)       // not yet
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 1)
    #expect(h.replied.contains { $0 == ("s2", "sent") })
}

@Test
func cancelDuringCountdownNeverSends() throws {
    let h = Harness()
    h.sender.handle(suggestionId: "s3", text: "稍等", autoSendOn: true, stale: false)
    h.sender.cancelPendingSend()
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s3", "cancelled") })
}

@Test
func appLeavingFrontmostAtSendTimeAborts() throws {
    // The race the countdown opens: user ⌘-Tabs away before it elapses.
    let h = Harness()
    h.sender.handle(suggestionId: "s4", text: "好的", autoSendOn: true, stale: false)
    h.frontmostAllowed = false        // user switched apps during the countdown
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s4", "cancelled") })
}

@Test
func secondAutoSendSupersedesAndCancelsThePrevious() throws {
    // Two suggestions inside the 5s window: the first pending send must be
    // cancelled (audit + its countdown), so a stale countdown can never also
    // fire — otherwise we'd double-send and corrupt the audit trail.
    let h = Harness()
    h.sender.handle(suggestionId: "first", text: "好的", autoSendOn: true, stale: false)
    let first = try #require(h.capturedCountdown)
    h.sender.handle(suggestionId: "second", text: "稍等", autoSendOn: true, stale: false)
    #expect(h.replied.contains { $0 == ("first", "cancelled") })

    // Even if a stale timer keeps ticking the old countdown, it must not send.
    for _ in 0..<Countdown.defaultSeconds { first.tick() }
    #expect(!h.replied.contains { $0 == ("first", "sent") })

    // The superseding send still completes normally — and only it pressed Return.
    let second = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { second.tick() }
    #expect(h.replied.contains { $0 == ("second", "sent") })
    #expect(h.mock.returns == 1)
}

@Test
func refusalPostsNoEventsAndSurfacesReason() {
    let h = Harness()
    h.frontmostAllowed = false
    h.sender.handle(suggestionId: "s5", text: "x", autoSendOn: false, stale: false)
    #expect(h.mock.clicks == 0 && h.mock.pastes == 0 && h.mock.returns == 0)
    #expect(h.refusals == [.wrongApp])
    #expect(h.replied.isEmpty)
}
