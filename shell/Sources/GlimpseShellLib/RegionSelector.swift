import AppKit

public enum ScreenCoords {
    /// Cocoa global coords (origin bottom-left of primary screen) →
    /// CG/SCK global coords (origin top-left of primary screen).
    public static func cgRect(fromCocoa rect: CGRect) -> CGRect {
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        return CGRect(
            x: rect.minX, y: primaryHeight - rect.maxY,
            width: rect.width, height: rect.height
        )
    }

    /// Cocoa global point (origin bottom-left) → CG global point (origin top-left).
    public static func cgPoint(fromCocoa p: CGPoint) -> CGPoint {
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        return CGPoint(x: p.x, y: primaryHeight - p.y)
    }
}

/// Borderless windows refuse key status by default, which silently swallows
/// every mouse event in the selection view — the overlay would render but
/// never respond to a drag.
final class KeyableWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

/// Full-screen drag-to-select. Calls back with the region in CG coordinates.
public final class RegionSelector {
    private var window: NSWindow?
    private let onDone: (CGRect) -> Void

    public init(onDone: @escaping (CGRect) -> Void) {
        self.onDone = onDone
    }

    public func begin() {
        let frame = NSScreen.screens.reduce(CGRect.zero) { $0.union($1.frame) }
        let win = KeyableWindow(
            contentRect: frame, styleMask: .borderless, backing: .buffered, defer: false
        )
        win.level = .screenSaver
        win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        win.isOpaque = false
        win.backgroundColor = NSColor.black.withAlphaComponent(0.15)
        win.contentView = SelectionView(frame: CGRect(origin: .zero, size: frame.size)) {
            [weak self] rectInWindow in
            guard let self, let win = self.window else { return }
            let globalOrigin = win.convertPoint(toScreen: rectInWindow.origin)
            let globalCocoa = CGRect(origin: globalOrigin, size: rectInWindow.size)
            self.window?.orderOut(nil)
            self.window = nil
            self.onDone(ScreenCoords.cgRect(fromCocoa: globalCocoa))
        }
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = win
    }
}

private final class SelectionView: NSView {
    private var start: NSPoint?
    private var current: NSPoint?
    private let onSelect: (CGRect) -> Void

    init(frame: CGRect, onSelect: @escaping (CGRect) -> Void) {
        self.onSelect = onSelect
        super.init(frame: frame)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {
        start = convert(event.locationInWindow, from: nil)
        current = start
    }

    override func mouseDragged(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        guard let rect = selectionRect(), rect.width > 20, rect.height > 20 else { return }
        onSelect(rect)
    }

    override func draw(_ dirtyRect: NSRect) {
        guard let rect = selectionRect() else { return }
        NSColor.systemBlue.withAlphaComponent(0.2).setFill()
        rect.fill()
        NSColor.systemBlue.setStroke()
        NSBezierPath(rect: rect).stroke()
    }

    private func selectionRect() -> CGRect? {
        guard let s = start, let c = current else { return nil }
        return CGRect(
            x: min(s.x, c.x), y: min(s.y, c.y),
            width: abs(s.x - c.x), height: abs(s.y - c.y)
        )
    }
}
