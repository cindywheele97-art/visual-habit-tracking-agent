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

/// Full-screen drag-to-select. Calls back with the region in CG coordinates, or nil on cancel.
public final class RegionSelector {
    private var window: NSWindow?
    private let onDone: (CGRect?) -> Void

    public init(onDone: @escaping (CGRect?) -> Void) {
        self.onDone = onDone
    }

    public static func isValidSelection(_ rect: CGRect) -> Bool {
        rect.width >= 10 && rect.height >= 10
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
        let view = SelectionView(frame: CGRect(origin: .zero, size: frame.size)) {
            [weak self] rectInWindow in
            guard let self, let win = self.window else { return }
            self.window?.orderOut(nil)
            self.window = nil
            guard let rectInWindow else {
                self.onDone(nil)
                return
            }
            let globalOrigin = win.convertPoint(toScreen: rectInWindow.origin)
            let globalCocoa = CGRect(origin: globalOrigin, size: rectInWindow.size)
            self.onDone(ScreenCoords.cgRect(fromCocoa: globalCocoa))
        }
        win.contentView = view
        win.makeKeyAndOrderFront(nil)
        view.window?.makeFirstResponder(view)
        NSApp.activate(ignoringOtherApps: true)
        window = win
    }
}

private final class SelectionView: NSView {
    private var start: NSPoint?
    private var current: NSPoint?
    private let onSelect: (CGRect?) -> Void

    init(frame: CGRect, onSelect: @escaping (CGRect?) -> Void) {
        self.onSelect = onSelect
        super.init(frame: frame)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override var acceptsFirstResponder: Bool { true }

    override func keyDown(with event: NSEvent) {
        if event.keyCode == 53 {
            onSelect(nil)
            return
        }
        super.keyDown(with: event)
    }

    override func mouseDown(with event: NSEvent) {
        start = convert(event.locationInWindow, from: nil)
        current = start
    }

    override func mouseDragged(with event: NSEvent) {
        current = convert(event.locationInWindow, from: nil)
        needsDisplay = true
    }

    override func mouseUp(with event: NSEvent) {
        guard let rect = selectionRect() else { return }
        if RegionSelector.isValidSelection(rect) {
            onSelect(rect)
        } else {
            onSelect(nil)
        }
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
