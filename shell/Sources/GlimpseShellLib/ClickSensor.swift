import AppKit
import CoreGraphics
import Foundation

/// The window a click landed in and the app that owns it — the unit the
/// capture allowlist judges. title/pid feed the flywheel join keys (window
/// title now, AX URL via pid).
public struct ClickTarget: Equatable {
    public let bundleId: String
    public let windowId: UInt32
    public let title: String
    public let pid: pid_t

    public init(bundleId: String, windowId: UInt32, title: String = "", pid: pid_t = 0) {
        self.bundleId = bundleId
        self.windowId = windowId
        self.title = title
        self.pid = pid
    }
}

/// Listen-only global left-click sensor. For clicks landing in a window OWNED
/// by an allowlisted app, takes a one-shot snapshot of that window at the
/// click, OCRs it, and emits a ClickMsg. Never modifies or blocks input.
public final class ClickSensor {
    /// Called when the event tap can't be created (Accessibility not granted).
    public var onPermissionNeeded: (() -> Void)?

    private let allowlist: AppAllowlist
    private let snapshotSize: CGSize
    private let onClick: (ClickMsg) -> Void
    private let resolveTarget: (CGEvent) -> ClickTarget?
    private let iso = ISO8601DateFormatter()
    private var tap: CFMachPort?
    private var tapSource: CFRunLoopSource?
    private var capturing = false  // main-thread-only: one in-flight capture at a time

    public init(
        allowlist: AppAllowlist,
        snapshotSize: CGSize = CGSize(width: 600, height: 400),
        resolveTarget: @escaping (CGEvent) -> ClickTarget? = ClickSensor.windowUnderPointer,
        onClick: @escaping (ClickMsg) -> Void
    ) {
        self.allowlist = allowlist
        self.snapshotSize = snapshotSize
        self.resolveTarget = resolveTarget
        self.onClick = onClick
    }

    /// The single gate for click capture. Fail-closed: no resolvable target
    /// window means no capture, and the CLICKED window's owner — not the
    /// frontmost app — must be allowlisted. (Clicking a password manager
    /// floating over WeChat targets the vault while WeChat stays frontmost;
    /// a frontmost gate would capture it.)
    static func captureDecision(target: ClickTarget?, allowlist: AppAllowlist) -> ClickTarget? {
        guard let target, allowlist.isAllowed(target.bundleId) else { return nil }
        return target
    }

    /// Default target resolution. Preferred source: the window-server
    /// annotation on the event (reliable because our tap listens at the
    /// ANNOTATED session point). Fallback when the field is absent: a
    /// front-to-back hit test over the on-screen window list.
    public static func windowUnderPointer(_ event: CGEvent) -> ClickTarget? {
        let number = event.getIntegerValueField(.mouseEventWindowUnderMousePointer)
        if number > 0, let windowId = UInt32(exactly: number),
            let target = owner(ofWindow: windowId)
        {
            return target
        }
        let info =
            CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID)
            as? [[String: Any]] ?? []
        guard
            let hit = firstWindow(at: event.location, in: info),
            let bundleId = NSRunningApplication(processIdentifier: hit.pid)?.bundleIdentifier
        else { return nil }
        return ClickTarget(bundleId: bundleId, windowId: hit.windowId, title: hit.title, pid: hit.pid)
    }

    private static func owner(ofWindow windowId: UInt32) -> ClickTarget? {
        guard
            let info = (CGWindowListCopyWindowInfo(.optionIncludingWindow, windowId)
                as? [[String: Any]])?.first,
            let pid = (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
            let bundleId = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier
        else { return nil }
        let title = info[kCGWindowName as String] as? String ?? ""
        return ClickTarget(bundleId: bundleId, windowId: windowId, title: title, pid: pid)
    }

    /// Front-to-back hit test (CGWindowList returns windows frontmost first).
    /// Pure — unit-tested with fabricated window lists.
    static func firstWindow(
        at point: CGPoint, in windowInfo: [[String: Any]]
    ) -> (pid: pid_t, windowId: UInt32, title: String)? {
        for info in windowInfo {
            guard
                let boundsDict = info[kCGWindowBounds as String] as? NSDictionary,
                let bounds = CGRect(dictionaryRepresentation: boundsDict),
                bounds.contains(point),
                let pid = (info[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value,
                let windowId = (info[kCGWindowNumber as String] as? NSNumber)?.uint32Value
            else { continue }
            return (pid, windowId, info[kCGWindowName as String] as? String ?? "")
        }
        return nil
    }

    /// Installs the tap on the main run loop. Call from the main thread after the
    /// app has finished launching.
    public func start() {
        let mask = CGEventMask(1 << CGEventType.leftMouseDown.rawValue)
        let refcon = Unmanaged.passUnretained(self).toOpaque()
        guard
            let tap = CGEvent.tapCreate(
                // Annotated point, not raw session entry: the window server
                // stamps routing info (window under pointer) onto events only
                // by the annotated tap — the wrong-window gate depends on it.
                tap: .cgAnnotatedSessionEventTap,
                place: .headInsertEventTap,
                options: .listenOnly,
                eventsOfInterest: mask,
                callback: { _, type, event, refcon in
                    if let refcon {
                        let sensor = Unmanaged<ClickSensor>.fromOpaque(refcon).takeUnretainedValue()
                        sensor.handle(type: type, event: event)
                    }
                    return Unmanaged.passUnretained(event)
                },
                userInfo: refcon
            )
        else {
            onPermissionNeeded?()
            return
        }
        self.tap = tap
        let source = CFMachPortCreateRunLoopSource(nil, tap, 0)
        self.tapSource = source
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    deinit {
        // Invalidate before the refcon (passUnretained self) can dangle.
        if let tap {
            CGEvent.tapEnable(tap: tap, enable: false)
            CFMachPortInvalidate(tap)
        }
        if let tapSource {
            CFRunLoopRemoveSource(CFRunLoopGetMain(), tapSource, .commonModes)
        }
    }

    // Runs on the main run loop (the tap's source is on the main loop).
    private func handle(type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return
        }
        guard type == .leftMouseDown else { return }
        guard
            let target = Self.captureDecision(
                target: resolveTarget(event), allowlist: allowlist
            )
        else { return }
        let point = event.location  // global, top-left origin (matches CG/SCK)
        let ts = iso.string(from: Date())
        let url = UrlReader.focusedDocumentURL(pid: target.pid)
        // The click FACT always survives (audit §三 click-stream completeness):
        // bursts and failed captures emit a metadata-only record instead of
        // vanishing — silent, behavior-correlated loss (fast comparison
        // clicking!) would bias every downstream flywheel metric. One snapshot
        // in flight at a time is still enforced; extra clicks coalesce to
        // metadata (capture_ok=false). `capturing` stays main-thread-only.
        guard !capturing else {
            onClick(
                ClickMsg(
                    ts: ts, app: target.bundleId,
                    x: Double(point.x), y: Double(point.y), blocks: [],
                    windowTitle: target.title, url: url, captureOk: false
                )
            )
            return
        }
        let size = snapshotSize
        capturing = true
        Task { [weak self] in
            guard let self else { return }
            let image = try? await ClickSnapshot.captureWindow(
                target.windowId, around: point, size: size
            )
            let blocks = image.flatMap { try? OCR.recognize($0) }
            let msg = ClickMsg(
                ts: ts, app: target.bundleId,
                x: Double(point.x), y: Double(point.y), blocks: blocks ?? [],
                windowTitle: target.title, url: url, captureOk: blocks != nil
            )
            self.onClick(msg)
            await MainActor.run { self.capturing = false }
        }
    }
}
