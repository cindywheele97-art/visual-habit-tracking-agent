import CoreGraphics
import Testing
@testable import GlimpseShellLib

private final class MockSyntheticInput: SyntheticInput {
    var ops: [String] = []
    var clicks = 0
    var pastes = 0
    var returns = 0
    func click(at point: CGPoint) { clicks += 1; ops.append("click") }
    func selectAll() { ops.append("selectAll") }
    func paste() { pastes += 1; ops.append("paste") }
    func pressReturn() { returns += 1; ops.append("return") }
}

/// Builds a Sender with controllable dependencies and captures its outputs.
/// readContact resolves synchronously with the current `contact` unless
/// `readOverride` is set, which lets a test capture the completion and fire it
/// later (simulating the fresh capture+OCR latency of a real read).
private final class Harness {
    let mock = MockSyntheticInput()
    var frontmostAllowed = true
    var contact = ""
    var readOverride: ((@escaping (String) -> Void) -> Void)?
    var capturedCountdown: Countdown?
    var replied: [(String, String)] = []
    var refusals: [RefuseReason] = []
    var pasteboard = ""
    lazy var sender = Sender(
        synthetic: mock,
        isFrontmostAllowed: { self.frontmostAllowed },
        inputBoxPoint: { CGPoint(x: 100, y: 200) },
        readContact: { completion in
            if let override = self.readOverride {
                override(completion)
            } else {
                completion(self.contact)
            }
        },
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
func fillSupersedesAnInFlightAutoSend() throws {
    // The kill-switch / fill-after-auto-send case: user starts an auto-send, then
    // switches to fill-only (toggle off → stale, or just fills another card). The
    // pending send MUST be cancelled so it can never fire Return behind their back.
    let h = Harness()
    h.sender.handle(suggestionId: "auto", text: "好的", autoSendOn: true, stale: false)
    let pending = try #require(h.capturedCountdown)
    h.sender.handle(suggestionId: "manual", text: "你好", autoSendOn: false, stale: false)
    #expect(h.replied.contains { $0 == ("auto", "cancelled") })
    #expect(h.replied.contains { $0 == ("manual", "fill") })

    // Even if a stale timer keeps ticking the superseded countdown, it must not send.
    for _ in 0..<Countdown.defaultSeconds { pending.tick() }
    #expect(h.mock.returns == 0)
    #expect(!h.replied.contains { $0 == ("auto", "sent") })
}

@Test
func fillReplacesAnyExistingDraftBeforePasting() {
    // WHY: the input box may already hold a half-typed human draft. Paste must
    // REPLACE it (select-all first) — otherwise auto-send fires Return on a
    // concatenation nobody approved.
    let h = Harness()
    h.sender.handle(suggestionId: "s1", text: "你好", autoSendOn: false, stale: false)
    #expect(h.mock.ops == ["click", "selectAll", "paste"])
}

@Test
func contactSwitchDuringCountdownAborts() throws {
    // WHY: the frontmost-app gate cannot tell conversations apart — a reply
    // drafted for 小明 must never be sent into 老王's chat opened mid-countdown.
    let h = Harness()
    h.contact = "小明"
    h.sender.handle(suggestionId: "s6", text: "好的", autoSendOn: true, stale: false)
    h.contact = "老王"  // user switched chats during the countdown
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s6", "cancelled") })
}

@Test
func sameContactAtSendTimeSends() throws {
    let h = Harness()
    h.contact = "小明"
    h.sender.handle(suggestionId: "s7", text: "好的", autoSendOn: true, stale: false)
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.replied.contains { $0 == ("s7", "sent") })
    #expect(h.mock.returns == 1)
}

@Test
func unknownContactStillSendsWhenOtherGatesPass() throws {
    // WHY: without a calibrated contact region the reader returns "" — the
    // contact gate degrades to the frontmost gate instead of bricking
    // auto-send for every uncalibrated user.
    let h = Harness()  // contact stays "" throughout
    h.sender.handle(suggestionId: "s8", text: "好的", autoSendOn: true, stale: false)
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.replied.contains { $0 == ("s8", "sent") })
}

@Test
func contactReadableAtArmButUnreadableAtSendAborts() throws {
    // WHY: fail-closed — if the contact region stops reading mid-countdown
    // (window moved/occluded), we can no longer prove the chat is unchanged.
    let h = Harness()
    h.contact = "小明"
    h.sender.handle(suggestionId: "s9", text: "好的", autoSendOn: true, stale: false)
    h.contact = ""
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s9", "cancelled") })
}

@Test
func escDuringContactVerificationNeverSends() throws {
    // WHY: the fresh contact read at fire time takes real time (capture+OCR).
    // Esc landing in that window must still win — the late read result must
    // not press Return after the user aborted, even when the contact matches.
    let h = Harness()
    h.contact = "小明"
    h.sender.handle(suggestionId: "s10", text: "好的", autoSendOn: true, stale: false)
    var fireRead: ((String) -> Void)?
    h.readOverride = { fireRead = $0 }  // defer only the fire-time read
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)  // verification still in flight
    h.sender.cancelPendingSend()  // Esc
    let proceed = try #require(fireRead)
    proceed("小明")  // fresh read finally resolves — and matches
    #expect(h.mock.returns == 0)
    #expect(h.replied.filter { $0 == ("s10", "cancelled") }.count == 1)
    #expect(!h.replied.contains { $0 == ("s10", "sent") })
}

@Test
func armReadResolvingLateStillAnchorsTheGate() throws {
    // WHY: the arm-time read is asynchronous too. Its late result must become
    // the comparison anchor — a send must be bound to the conversation that
    // was on screen when the user clicked, not to a placeholder or a cached
    // value that may lag a chat switch.
    let h = Harness()
    var armRead: ((String) -> Void)?
    h.readOverride = { armRead = $0 }  // defer the ARM read
    h.sender.handle(suggestionId: "s13", text: "好的", autoSendOn: true, stale: false)
    h.readOverride = nil  // later reads resolve synchronously from `contact`
    let arm = try #require(armRead)
    arm("小明")  // what was actually on screen at arm time
    h.contact = "小明"  // unchanged at fire time
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.replied.contains { $0 == ("s13", "sent") })
    #expect(h.mock.returns == 1)
}

@Test
func staleSuggestionsUpdateCancelsArmedSend() throws {
    // WHY: the brain flags shown cards stale when the conversation moves on;
    // an armed countdown then holds an outdated draft and must be aborted —
    // this is the shell half of the stale gate.
    let h = Harness()
    h.sender.handle(suggestionId: "s11", text: "好的", autoSendOn: true, stale: false)
    h.sender.noteSuggestionsUpdate(stale: true)
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s11", "cancelled") })
}

@Test
func freshSuggestionsUpdateDoesNotCancelArmedSend() throws {
    let h = Harness()
    h.sender.handle(suggestionId: "s12", text: "好的", autoSendOn: true, stale: false)
    h.sender.noteSuggestionsUpdate(stale: false)
    let cd = try #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.replied.contains { $0 == ("s12", "sent") })
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
