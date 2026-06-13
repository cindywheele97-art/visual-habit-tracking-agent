import CoreGraphics

/// Orchestrates a fill/send: asks SendPlanner, then drives SyntheticInput, the
/// Countdown, and the audit callback. All OS access is injected so the logic is
/// testable. Main-thread use only (UI-driven).
public final class Sender {
    private let synthetic: SyntheticInput
    private let isFrontmostAllowed: () -> Bool
    private let inputBoxPoint: () -> CGPoint?
    private let accessibilityTrusted: () -> Bool
    private let setPasteboard: (String) -> Void
    private let presentRefusal: (RefuseReason) -> Void
    private let presentCountdown: (Countdown) -> Void
    private let emitReplied: (String, String) -> Void
    private let countdownSeconds: Int

    private var pending: (countdown: Countdown, suggestionId: String)?

    public init(
        synthetic: SyntheticInput,
        isFrontmostAllowed: @escaping () -> Bool,
        inputBoxPoint: @escaping () -> CGPoint?,
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
            pending = (countdown, suggestionId)
            presentCountdown(countdown)
        }
    }

    /// Cancel a pending auto-send (Esc, the kill-switch, or app-switch).
    public func cancelPendingSend() {
        guard let pending else { return }
        pending.countdown.cancel()
        emitReplied(pending.suggestionId, "cancelled")
        self.pending = nil
    }

    private func fill(_ text: String) {
        guard let point = inputBoxPoint() else { return }  // already verified in plan
        setPasteboard(text)
        synthetic.click(at: point)
        synthetic.paste()
    }

    private func finalizeSend(_ suggestionId: String) {
        defer { pending = nil }
        // Re-check at send time: the user may have ⌘-Tabbed away during the
        // countdown. Never press Return into whatever else now owns the screen.
        guard isFrontmostAllowed() else {
            emitReplied(suggestionId, "cancelled")
            return
        }
        synthetic.pressReturn()
        emitReplied(suggestionId, "sent")
    }
}
