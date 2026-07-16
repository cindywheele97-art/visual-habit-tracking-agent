import AppKit
import ApplicationServices
import CoreGraphics

/// One closed attention interval on a (app, window, url) context.
public struct DwellInterval: Equatable {
    public let app: String
    public let title: String
    public let url: String
    public let start: Double  // epoch seconds
    public let end: Double

    public var seconds: Double { end - start }

    public init(app: String, title: String, url: String, start: Double, end: Double) {
        self.app = app
        self.title = title
        self.url = url
        self.start = start
        self.end = end
    }
}

/// Pure attention-interval logic (audit §三 B2). The sensor feeds focus
/// transitions and idle boundaries with epoch timestamps; the tracker emits a
/// closed interval when attention leaves a context and it lasted >= minSeconds.
/// Deliberately OS-free so the semantics the analytics layer treats as ground
/// truth are unit-tested.
public final class DwellTracker {
    private let minSeconds: Double
    private var current: (app: String, title: String, url: String, start: Double)?

    public init(minSeconds: Double = 2.0) {
        self.minSeconds = minSeconds
    }

    /// `app == nil` means attention left tracked surfaces (non-allowlisted app
    /// or no focus). An unchanged (app, title, url) is a polling no-op — one
    /// continuous interval, never fragments.
    public func focusChanged(
        app: String?, title: String, url: String, at t: Double
    ) -> DwellInterval? {
        if let cur = current, let app, cur.app == app, cur.title == title, cur.url == url {
            return nil
        }
        let closed = close(at: t)
        if let app {
            current = (app, title, url, t)
        }
        return closed
    }

    /// Idle began: close the open interval at `t` — the LAST-ACTIVITY moment,
    /// not the (later) instant the idle threshold fired. Walking away must not
    /// count as attention.
    public func idled(at t: Double) -> DwellInterval? {
        close(at: t)
    }

    private func close(at t: Double) -> DwellInterval? {
        guard let c = current else { return nil }
        current = nil
        guard t - c.start >= minSeconds else { return nil }  // flick-through noise
        return DwellInterval(app: c.app, title: c.title, url: c.url, start: c.start, end: t)
    }
}

/// Best-effort URL of an app's focused window via Accessibility (AXDocument).
/// Safari and document-based apps expose it; Chrome usually does not — the
/// browser-extension tier (P7.4) is the real fix, this is the AX stopgap.
/// Requires the Accessibility permission the click tap already needs.
public enum UrlReader {
    public static func focusedDocumentURL(pid: pid_t) -> String {
        let app = AXUIElementCreateApplication(pid)
        var windowRef: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                app, kAXFocusedWindowAttribute as CFString, &windowRef
            ) == .success,
            let windowRef,
            CFGetTypeID(windowRef) == AXUIElementGetTypeID()
        else { return "" }
        let window = unsafeDowncast(windowRef, to: AXUIElement.self)
        var docRef: CFTypeRef?
        guard
            AXUIElementCopyAttributeValue(
                window, kAXDocumentAttribute as CFString, &docRef
            ) == .success,
            let url = docRef as? String
        else { return "" }
        return url
    }
}

/// OS wiring for dwell: polls frontmost app + focused-window title + idle age
/// on a main-run-loop Timer and drives the pure DwellTracker. Allowlist-gated:
/// non-allowlisted apps read as "attention left" — we only ever record apps
/// the operator opted in (capture correctness, unchanged by the privacy freeze).
public final class AttentionSensor {
    private let allowlist: AppAllowlist
    private let tracker = DwellTracker()
    private let onDwell: (DwellInterval) -> Void
    private let idleThreshold: TimeInterval
    private var timer: Timer?
    private var idleClosed = false

    public init(
        allowlist: AppAllowlist,
        idleThreshold: TimeInterval = 60,
        onDwell: @escaping (DwellInterval) -> Void
    ) {
        self.allowlist = allowlist
        self.idleThreshold = idleThreshold
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
            if let interval = tracker.focusChanged(app: nil, title: "", url: "", at: now) {
                onDwell(interval)
            }
            return
        }
        let pid = front.processIdentifier
        let title = Self.frontWindowTitle(pid: pid)
        let url = UrlReader.focusedDocumentURL(pid: pid)
        if let interval = tracker.focusChanged(app: bundle, title: title, url: url, at: now) {
            onDwell(interval)
        }
    }

    /// Seconds since the user last produced any of the common input events.
    /// (kCGAnyInputEventType is a C macro Swift can't import — take the min
    /// over the event types that constitute "activity".)
    static func secondsSinceLastInput() -> TimeInterval {
        let types: [CGEventType] = [.leftMouseDown, .rightMouseDown, .mouseMoved, .scrollWheel, .keyDown]
        return types.map {
            CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0)
        }.min() ?? 0
    }

    /// Title of the app's frontmost layer-0 window (window names are readable
    /// because the app already holds the screen-recording permission).
    static func frontWindowTitle(pid: pid_t) -> String {
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
