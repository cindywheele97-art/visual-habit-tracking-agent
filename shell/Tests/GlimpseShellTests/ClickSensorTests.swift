import CoreGraphics
import Foundation
import Testing
@testable import GlimpseShellLib

private func allowlist(_ ids: [String]) throws -> AppAllowlist {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let file = dir.appendingPathComponent("allowlist.json")
    let json = "[" + ids.map { "\"\($0)\"" }.joined(separator: ",") + "]"
    try json.write(to: file, atomically: true, encoding: .utf8)
    return AppAllowlist(path: file)
}

@Test
func captureDecisionRequiresTheClickedWindowsOwnerOnTheAllowlist() throws {
    // WHY: the frontmost app is NOT the click target — clicking a background
    // window (e.g. a password manager floating over WeChat) delivers the click
    // to that window while WeChat stays frontmost. The privacy gate must judge
    // the app that OWNS the clicked window.
    let list = try allowlist(["com.tencent.xinWeChat"])
    let wechat = ClickTarget(bundleId: "com.tencent.xinWeChat", windowId: 42)
    let vault = ClickTarget(bundleId: "com.1password.1password", windowId: 7)
    #expect(ClickSensor.captureDecision(target: wechat, allowlist: list) == wechat)
    #expect(ClickSensor.captureDecision(target: vault, allowlist: list) == nil)
}

@Test
func firstWindowHitTestsFrontToBack() {
    // WHY: when the event lacks the window-server annotation, the fallback
    // must pick the FRONTMOST window containing the click — the list order is
    // front-to-back, and picking a rear window would misattribute the click
    // (and capture) to an app the user didn't click.
    func window(_ rect: CGRect, pid: Int32, number: UInt32) -> [String: Any] {
        [
            kCGWindowBounds as String: rect.dictionaryRepresentation,
            kCGWindowOwnerPID as String: NSNumber(value: pid),
            kCGWindowNumber as String: NSNumber(value: number),
        ]
    }
    let floating = window(CGRect(x: 0, y: 0, width: 100, height: 100), pid: 111, number: 7)
    let chat = window(CGRect(x: 0, y: 0, width: 500, height: 500), pid: 222, number: 9)

    let onFloating = ClickSensor.firstWindow(at: CGPoint(x: 50, y: 50), in: [floating, chat])
    #expect(onFloating?.pid == 111 && onFloating?.windowId == 7)

    let onChat = ClickSensor.firstWindow(at: CGPoint(x: 300, y: 300), in: [floating, chat])
    #expect(onChat?.pid == 222 && onChat?.windowId == 9)

    #expect(ClickSensor.firstWindow(at: CGPoint(x: 900, y: 900), in: [floating, chat]) == nil)
}

@Test
func captureDecisionFailsClosedWithoutAResolvableWindow() throws {
    // WHY: if the OS cannot tell us which window was clicked, we cannot prove
    // the target is allowlisted — capture nothing.
    let list = try allowlist(["com.tencent.xinWeChat"])
    #expect(ClickSensor.captureDecision(target: nil, allowlist: list) == nil)
}
