import AppKit

/// Full-screen single-click capture. Calls back with the clicked point in CG
/// coordinates (top-left origin) — the chat input-box location to type into.
public final class InputBoxCalibrator {
    private var window: NSWindow?
    private let onDone: (CGPoint) -> Void

    public init(onDone: @escaping (CGPoint) -> Void) {
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
        win.backgroundColor = NSColor.systemGreen.withAlphaComponent(0.12)
        win.contentView = PointSelectorView(frame: CGRect(origin: .zero, size: frame.size)) {
            [weak self] pointInWindow in
            guard let self, let win = self.window else { return }
            let globalCocoa = win.convertPoint(toScreen: pointInWindow)
            self.window?.orderOut(nil)
            self.window = nil
            self.onDone(ScreenCoords.cgPoint(fromCocoa: globalCocoa))
        }
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = win
    }
}

private final class PointSelectorView: NSView {
    private let onPick: (NSPoint) -> Void

    init(frame: CGRect, onPick: @escaping (NSPoint) -> Void) {
        self.onPick = onPick
        super.init(frame: frame)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("not used") }

    override func mouseDown(with event: NSEvent) {
        onPick(event.locationInWindow)
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.systemGreen.withAlphaComponent(0.08).setFill()
        dirtyRect.fill()
    }
}
