import AppKit
import CoreGraphics
import Foundation

/// Listen-only global left-click sensor. For clicks whose foreground app is on
/// the allowlist, takes a one-shot snapshot at the click, OCRs it, and emits a
/// ClickMsg. Never modifies or blocks input.
public final class ClickSensor {
    /// Called when the event tap can't be created (Accessibility not granted).
    public var onPermissionNeeded: (() -> Void)?

    private let allowlist: AppAllowlist
    private let snapshotSize: CGSize
    private let onClick: (ClickMsg) -> Void
    private let iso = ISO8601DateFormatter()
    private var tap: CFMachPort?

    public init(
        allowlist: AppAllowlist,
        snapshotSize: CGSize = CGSize(width: 600, height: 400),
        onClick: @escaping (ClickMsg) -> Void
    ) {
        self.allowlist = allowlist
        self.snapshotSize = snapshotSize
        self.onClick = onClick
    }

    /// Installs the tap on the main run loop. Call from the main thread after the
    /// app has finished launching.
    public func start() {
        let mask = CGEventMask(1 << CGEventType.leftMouseDown.rawValue)
        let refcon = Unmanaged.passUnretained(self).toOpaque()
        guard
            let tap = CGEvent.tapCreate(
                tap: .cgSessionEventTap,
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
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    // Runs on the main run loop (the tap's source is on the main loop).
    private func handle(type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return
        }
        guard type == .leftMouseDown else { return }
        let bundleId = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        guard allowlist.isAllowed(bundleId), let app = bundleId else { return }
        let point = event.location  // global, top-left origin (matches CG/SCK)
        let ts = iso.string(from: Date())
        let size = snapshotSize
        Task { [weak self] in
            guard let self else { return }
            guard
                let image = try? await ClickSnapshot.captureAround(point: point, size: size),
                let blocks = try? OCR.recognize(image)
            else { return }
            let msg = ClickMsg(
                ts: ts, app: app, x: Double(point.x), y: Double(point.y), blocks: blocks
            )
            self.onClick(msg)
        }
    }
}
