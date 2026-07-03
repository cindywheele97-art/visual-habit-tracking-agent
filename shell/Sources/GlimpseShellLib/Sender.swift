import CoreGraphics

/// Orchestrates a fill/send: asks SendPlanner, then drives SyntheticInput, the
/// Countdown, and the audit callback. All OS access is injected so the logic is
/// testable. Main-thread use only (UI-driven).
public final class Sender {
    private let synthetic: SyntheticInput
    private let isFrontmostAllowed: () -> Bool
    private let inputBoxPoint: () -> CGPoint?
    private let readContact: (@escaping (String) -> Void) -> Void
    private let accessibilityTrusted: () -> Bool
    private let setPasteboard: (String) -> Void
    private let presentRefusal: (RefuseReason) -> Void
    private let presentCountdown: (Countdown) -> Void
    private let emitReplied: (String, String) -> Void
    private let countdownSeconds: Int

    private var pending: (countdown: Countdown, suggestionId: String, contact: String)?

    public init(
        synthetic: SyntheticInput,
        isFrontmostAllowed: @escaping () -> Bool,
        inputBoxPoint: @escaping () -> CGPoint?,
        readContact: @escaping (_ completion: @escaping (String) -> Void) -> Void,
        accessibilityTrusted: @escaping () -> Bool,
        setPasteboard: @escaping (String) -> Void,
        presentRefusal: @escaping (RefuseReason) -> Void,
        presentCountdown: @escaping (Countdown) -> Void,
        emitReplied: @escaping (_ suggestionId: String, _ mode: String) -> Void,
        countdownSeconds: Int = Countdown.defaultSeconds
    ) {
        self.synthetic = synthetic
        self.isFrontmostAllowed = isFrontmostAllowed
        self.inputBoxPoint = inputBoxPoint
        self.readContact = readContact
        self.accessibilityTrusted = accessibilityTrusted
        self.setPasteboard = setPasteboard
        self.presentRefusal = presentRefusal
        self.presentCountdown = presentCountdown
        self.emitReplied = emitReplied
        self.countdownSeconds = countdownSeconds
    }

    public func handle(suggestionId: String, text: String, autoSendOn: Bool, stale: Bool) {
        let context = SendContext(
            frontmostAllowed: isFrontmostAllowed(),
            calibrated: inputBoxPoint() != nil,
            accessibilityTrusted: accessibilityTrusted(),
            autoSendOn: autoSendOn,
            stale: stale
        )
        switch SendPlanner.plan(context) {
        case .refuse(let reason):
            presentRefusal(reason)
        case .fill:
            // A fill supersedes any in-flight auto-send: if the user started an
            // auto-send then switched to fill-only (toggle off, or stale), the
            // old countdown must not later fire Return behind their back.
            cancelPendingSend()
            fill(text)
            emitReplied(suggestionId, "fill")
        case .fillThenSend:
            // A new auto-send supersedes any in-flight one: cancel it (audit +
            // stop its countdown) so a stale countdown can never also fire and
            // double-send. No-op when nothing is pending.
            cancelPendingSend()
            fill(text)
            let countdown = Countdown(seconds: countdownSeconds) { [weak self] in
                self?.finalizeSend(suggestionId)
            }
            pending = (countdown, suggestionId, contact: "")
            // Bind the send to the conversation actually on screen NOW: a
            // fresh read at BOTH endpoints (arm and fire), never a poll cache
            // that can lag a chat switch. The read resolves well inside the
            // countdown; if it cannot (capture failure), the "" placeholder
            // fails closed at fire time for any calibrated contact region.
            readContact { [weak self] name in
                guard let self, self.pending?.countdown === countdown else { return }
                self.pending = (countdown, suggestionId, name)
            }
            presentCountdown(countdown)
        }
    }

    /// Cancel a pending auto-send (Esc, the kill-switch, or app-switch).
    /// Also aborts a send whose contact verification is still in flight: the
    /// late verification result finds `pending` cleared and does nothing.
    public func cancelPendingSend() {
        guard let pending else { return }
        pending.countdown.cancel()
        emitReplied(pending.suggestionId, "cancelled")
        self.pending = nil
    }

    /// Called for every suggestions update from the brain. A stale update means
    /// the conversation moved on — an armed auto-send holds an outdated draft
    /// and must be aborted. This is the shell half of the stale gate.
    public func noteSuggestionsUpdate(stale: Bool) {
        if stale { cancelPendingSend() }
    }

    private func fill(_ text: String) {
        guard let point = inputBoxPoint() else { return }  // already verified in plan
        setPasteboard(text)
        synthetic.click(at: point)
        // Replace, never append: the box may hold a half-typed human draft that
        // must not be concatenated into an approved (possibly auto-sent) reply.
        synthetic.selectAll()
        synthetic.paste()
    }

    private func finalizeSend(_ suggestionId: String) {
        guard let armed = pending, armed.suggestionId == suggestionId else { return }
        // The frontmost gate cannot tell conversations apart — verify with a
        // FRESH read before the irreversible Return. `pending` stays set while
        // the read is in flight so Esc/supersede in that window still wins:
        // they clear or replace it, and the late resolution below backs off.
        readContact { [weak self] fresh in
            guard let self else { return }
            guard let current = self.pending, current.countdown === armed.countdown else {
                return  // cancelled or superseded while verifying — already audited
            }
            self.pending = nil
            // Re-check at send time: the user may have ⌘-Tabbed away or
            // switched chats during the countdown/verification. Never press
            // Return into whatever else now owns the screen. ""=="" passes:
            // with no calibrated contact region both reads are "" and the gate
            // degrades to the frontmost check rather than bricking auto-send.
            guard self.isFrontmostAllowed(), fresh == current.contact else {
                self.emitReplied(suggestionId, "cancelled")
                return
            }
            self.synthetic.pressReturn()
            self.emitReplied(suggestionId, "sent")
        }
    }
}
