import AppKit
import CoreGraphics

/// One closed attention interval on an app. `title` is the last window title
/// observed during the interval — descriptive metadata, not an interval
/// boundary (titles flicker: notification counters, blinking chat titles).
public struct DwellInterval: Equatable {
    public let app: String
    public let title: String
    public let start: Double  // epoch seconds
    public let end: Double

    public var seconds: Double { end - start }

    public init(app: String, title: String, start: Double, end: Double) {
        self.app = app
        self.title = title
        self.start = start
        self.end = end
    }
}

/// Pure attention-interval logic (audit §三 B2). Intervals split on APP change
/// only — per-page dwell inside one browser needs the P7.4 extension's Page
/// Visibility API; the native tier can only bound attention by app focus + idle.
/// OS-free so the semantics the analytics layer trusts are unit-tested.
public final class DwellTracker {
    private let minSeconds: Double
    private var current: (app: String, title: String, start: Double)?

    public init(minSeconds: Double = 2.0) {
        self.minSeconds = minSeconds
    }

    /// `app == nil` means attention left tracked apps. Same app = one continuous
    /// interval; only the last-seen title is retained. Returns a closed
    /// interval when attention leaves an app that was held >= minSeconds.
    public func focusChanged(app: String?, title: String, at t: Double) -> DwellInterval? {
        if let cur = current, cur.app == app {
            current = (cur.app, title, cur.start)  // same app, refresh title only
            return nil
        }
        let closed = close(at: t)
        if let app {
            current = (app, title, t)
        }
        return closed
    }

    /// Idle began: close the open interval at `t` — the LAST-ACTIVITY moment,
    /// not the (later) instant idle was detected. When the user goes hands-off
    /// right after a focus tick, `t` can precede the interval start; the
    /// minSeconds guard in close() rejects that (a negative or zero span is
    /// never >= minSeconds), so no corrupt interval is ever emitted.
    public func idled(at t: Double) -> DwellInterval? {
        close(at: t)
    }

    private func close(at t: Double) -> DwellInterval? {
        guard let c = current else { return nil }
        current = nil
        // Sole gate: an out-of-order (t < start) or flick-through close has a
        // span < minSeconds and is dropped. Keep this a `>=` on the raw span —
        // do NOT abs() it, or negative-duration closes would leak as intervals.
        guard t - c.start >= minSeconds else { return nil }
        return DwellInterval(app: c.app, title: c.title, start: c.start, end: t)
    }
}

/// OS wiring for dwell: polls frontmost app + focused-window title + input idle
/// on a main-run-loop Timer and drives the pure DwellTracker. Allowlist-gated —
/// only opted-in apps are ever observed. Deliberately NO cross-process AX calls
/// (the URL is the P7.4 extension's job); the only per-tick OS work is a
/// same-process CGWindowList scan for the title, the cost the click path
/// already pays per click.
public final class AttentionSensor {
    private let allowlist: AppAllowlist
    private let onDwell: (DwellInterval) -> Void
    private let idleThreshold: TimeInterval
    private let tracker: DwellTracker
    private var timer: Timer?
    private var idleClosed = false

    public init(
        allowlist: AppAllowlist,
        idleThreshold: TimeInterval = 60,
        onDwell: @escaping (DwellInterval) -> Void
    ) {
        self.allowlist = allowlist
        self.idleThreshold = idleThreshold
        self.tracker = DwellTracker()
        self.onDwell = onDwell
    }

    public func start(interval: TimeInterval = 2) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.tick()
        }
        tick()
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func tick() {
        let now = Date().timeIntervalSince1970
        let idle = Self.secondsSinceLastInput()
        if idle >= idleThreshold {
            if !idleClosed, let interval = tracker.idled(at: now - idle) {
                onDwell(interval)
            }
            idleClosed = true
            return
        }
        idleClosed = false
        guard
            let front = NSWorkspace.shared.frontmostApplication,
            let bundle = front.bundleIdentifier,
            allowlist.isAllowed(bundle)
        else {
            if let interval = tracker.focusChanged(app: nil, title: "", at: now) {
                onDwell(interval)
            }
            return
        }
        let title = Self.frontWindowTitle(pid: front.processIdentifier)
        if let interval = tracker.focusChanged(app: bundle, title: title, at: now) {
            onDwell(interval)
        }
    }

    /// Seconds since the user last produced HARDWARE input. .hidSystemState
    /// (not .combinedSessionState) so the shell's own auto-send synthetic
    /// keystrokes do not reset the idle clock and fabricate phantom dwell while
    /// the operator is away. (kCGAnyInputEventType is a C macro Swift can't
    /// import — take the min over the event types that constitute activity.)
    static func secondsSinceLastInput() -> TimeInterval {
        let types: [CGEventType] = [.leftMouseDown, .rightMouseDown, .mouseMoved, .scrollWheel, .keyDown]
        return types.map {
            CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: $0)
        }.min() ?? 0
    }

    /// Title of the app's frontmost layer-0 window (same-process CGWindowList
    /// scan — no cross-process IPC; window names readable via the existing
    /// screen-recording permission). Also the best-effort product_key for a
    /// 选品 outcome (P7.3).
    public static func frontWindowTitle(pid: pid_t) -> String {
        let info =
            CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID)
            as? [[String: Any]] ?? []
        for window in info {
            guard
                (window[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == pid,
                (window[kCGWindowLayer as String] as? NSNumber)?.intValue == 0
            else { continue }
            return window[kCGWindowName as String] as? String ?? ""
        }
        return ""
    }
}
