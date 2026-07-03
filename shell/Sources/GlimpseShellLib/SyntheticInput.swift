import CoreGraphics

/// The Computer-Use primitive: the only unit that posts OS input events.
/// Injected as a protocol so Sender's logic is testable with a mock.
public protocol SyntheticInput {
    /// Click at a CG global point (top-left origin) — focuses the field and
    /// confirms which window receives subsequent input.
    func click(at point: CGPoint)
    /// Select the field's existing contents (⌘A) so the next paste REPLACES
    /// them — a pre-existing human draft must never be concatenated into a send.
    func selectAll()
    /// Paste the current pasteboard contents (⌘V).
    func paste()
    /// Press Return — the irreversible send in auto-send mode.
    func pressReturn()
}

/// Production implementation backed by CGEvent. Requires Accessibility trust,
/// which the app already holds for click-tracking.
public final class CGEventSyntheticInput: SyntheticInput {
    private let aKeyCode: CGKeyCode = 0   // 'a'
    private let vKeyCode: CGKeyCode = 9   // 'v'
    private let returnKeyCode: CGKeyCode = 36

    public init() {}

    public func click(at point: CGPoint) {
        let source = CGEventSource(stateID: .combinedSessionState)
        let down = CGEvent(
            mouseEventSource: source, mouseType: .leftMouseDown,
            mouseCursorPosition: point, mouseButton: .left
        )
        let up = CGEvent(
            mouseEventSource: source, mouseType: .leftMouseUp,
            mouseCursorPosition: point, mouseButton: .left
        )
        down?.post(tap: .cgSessionEventTap)
        up?.post(tap: .cgSessionEventTap)
    }

    public func selectAll() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let down = CGEvent(keyboardEventSource: source, virtualKey: aKeyCode, keyDown: true)
        let up = CGEvent(keyboardEventSource: source, virtualKey: aKeyCode, keyDown: false)
        // Virtual keycodes are positional: keycode 0 is 'a' on QWERTY but 'q'
        // on AZERTY — and ⌘Q would QUIT the chat app. Stamping the unicode
        // string makes AppKit's key-equivalent matching see ⌘A on any layout.
        var chars: [UniChar] = [0x0061]  // 'a'
        down?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &chars)
        up?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &chars)
        down?.flags = .maskCommand
        up?.flags = .maskCommand
        down?.post(tap: .cgSessionEventTap)
        up?.post(tap: .cgSessionEventTap)
    }

    public func paste() {
        let source = CGEventSource(stateID: .combinedSessionState)
        let down = CGEvent(keyboardEventSource: source, virtualKey: vKeyCode, keyDown: true)
        down?.flags = .maskCommand
        let up = CGEvent(keyboardEventSource: source, virtualKey: vKeyCode, keyDown: false)
        up?.flags = .maskCommand
        down?.post(tap: .cgSessionEventTap)
        up?.post(tap: .cgSessionEventTap)
    }

    public func pressReturn() {
        let source = CGEventSource(stateID: .combinedSessionState)
        CGEvent(keyboardEventSource: source, virtualKey: returnKeyCode, keyDown: true)?
            .post(tap: .cgSessionEventTap)
        CGEvent(keyboardEventSource: source, virtualKey: returnKeyCode, keyDown: false)?
            .post(tap: .cgSessionEventTap)
    }
}
