/// A countdown ticked externally (one tick per second by the UI timer), so the
/// abort logic is testable without real time. Fires `onComplete` exactly once
/// when it reaches zero, unless cancelled first.
public final class Countdown {
    public static let defaultSeconds = 5

    public private(set) var remaining: Int
    private let onComplete: () -> Void
    private var done = false

    public init(seconds: Int = Countdown.defaultSeconds, onComplete: @escaping () -> Void) {
        self.remaining = seconds
        self.onComplete = onComplete
    }

    /// Whether the countdown has resolved (completed or cancelled).
    public var isFinished: Bool { done }

    public func tick() {
        guard !done else { return }
        remaining -= 1
        if remaining <= 0 {
            done = true
            onComplete()
        }
    }

    public func cancel() {
        guard !done else { return }
        done = true
    }
}
