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
    private var tapSource: CFRunLoopSource?
    private var capturing = false  // main-thread-only: one in-flight capture at a time

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
        // Drop burst clicks: keep one capture in flight at a time. Avoids piling
        // up concurrent SCK+OCR work and out-of-order onClick delivery. (capturing
        // is only ever touched on the main thread — here and in the reset below.)
        guard !capturing else { return }
        let bundleId = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
        guard allowlist.isAllowed(bundleId), let app = bundleId else { return }
        let point = event.location  // global, top-left origin (matches CG/SCK)
        let ts = iso.string(from: Date())
        let size = snapshotSize
        capturing = true
        Task { [weak self] in
            guard let self else { return }
            let image = try? await ClickSnapshot.captureAround(point: point, size: size)
            let blocks = image.flatMap { try? OCR.recognize($0) }
            if let blocks {
                let msg = ClickMsg(
                    ts: ts, app: app, x: Double(point.x), y: Double(point.y), blocks: blocks
                )
                self.onClick(msg)
            }
            await MainActor.run { self.capturing = false }
        }
    }
}
