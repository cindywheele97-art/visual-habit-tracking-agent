import AppKit
import Foundation
import Testing
@testable import GlimpseShellLib

private func renderChatImage() -> CGImage {
    let size = CGSize(width: 800, height: 200)
    let image = NSImage(size: size)
    image.lockFocus()
    NSColor.white.setFill()
    NSRect(origin: .zero, size: size).fill()
    let attrs: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: 28), .foregroundColor: NSColor.black,
    ]
    // Customer text on the left, our reply on the right.
    ("Hello there" as NSString).draw(at: NSPoint(x: 20, y: 120), withAttributes: attrs)
    ("OK thanks" as NSString).draw(at: NSPoint(x: 600, y: 40), withAttributes: attrs)
    image.unlockFocus()
    var rect = CGRect(origin: .zero, size: size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)!
}

// WHY: x-extents drive inbound/outbound classification in the brain; if
// OCR stops reporting sane geometry, side detection silently breaks.
@Test
func recognizesTextWithGeometry() throws {
    let blocks = try OCR.recognize(renderChatImage())
    let texts = blocks.map(\.text).joined(separator: " ")
    #expect(texts.localizedCaseInsensitiveContains("hello"), "got: \(texts)")
    guard let hello = blocks.first(where: { $0.text.localizedCaseInsensitiveContains("hello") })
    else {
        #expect(Bool(false), "no hello block")
        return
    }
    #expect((hello.x0 + hello.x1) / 2 < 0.5)  // left side
    #expect(hello.conf > 0.3)
    // Top line ("Hello there", drawn higher) must come before lower lines.
    #expect(blocks.first!.text.localizedCaseInsensitiveContains("hello"))
}
