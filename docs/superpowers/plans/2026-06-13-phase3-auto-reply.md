# Phase 3 — Computer-Use Auto-Reply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a chosen reply suggestion into text placed in (fill-only, default) or sent from (opt-in auto-send) the real chat input box, via synthetic input, with fail-closed safety guards.

**Architecture:** Shell-side Swift only; the brain gains one `replied` audit message. The dangerous decision logic lives in one pure unit (`SendPlanner`); OS event injection is a thin protocol-injected leaf (`SyntheticInput`). Reuses the existing `AppAllowlist` for a fail-closed frontmost-app check. Auto-send is gated by an off-by-default toggle, a 5s Esc-cancellable countdown, a stale-block, and a re-check of the frontmost app at send time.

**Tech Stack:** Swift 5.9 / AppKit / SwiftUI / Swift Testing (shell); Python 3.11 / pydantic / pytest (brain); `CGEvent` for synthetic input.

**Spec:** `docs/superpowers/specs/2026-06-13-phase3-auto-reply-design.md`

---

## File Structure

**Brain (Python):**
- Modify `brain/src/glimpse_brain/protocol.py` — add `RepliedMsg`, extend `InboundMsg`.
- Modify `brain/src/glimpse_brain/server.py` — dispatch `RepliedMsg` → audit log.
- Modify `brain/tests/test_protocol.py`, `brain/tests/test_server.py`.

**Shell (Swift) — new files in `shell/Sources/GlimpseShellLib/`:**
- `InputBoxStore.swift` — persist calibrated input-box point.
- `SendPlanner.swift` — pure `SendPlan` decision + `RefuseReason` + `SendContext`.
- `Countdown.swift` — externally-ticked countdown with cancel.
- `SyntheticInput.swift` — protocol + `CGEvent` implementation.
- `Sender.swift` — orchestrator.
- `InputBoxCalibrator.swift` — one-time single-click calibration UI.

**Shell — modified:**
- `shell/Sources/GlimpseShellLib/Protocol.swift` — add `RepliedMsg`.
- `shell/Sources/GlimpseShellLib/RegionSelector.swift` — add `ScreenCoords.cgPoint`, make `KeyableWindow` internal.
- `shell/Sources/GlimpseShellLib/Overlay.swift` — fill/send button, countdown banner, `onAct`.
- `shell/Sources/GlimpseShell/main.swift` — menu items, auto-send toggle, `Sender` wiring.

**Shell — new tests in `shell/Tests/GlimpseShellTests/`:**
- `InputBoxStoreTests.swift`, `SendPlannerTests.swift`, `CountdownTests.swift`, `SenderTests.swift`
- additions to `ProtocolTests.swift` (RepliedMsg encode, cgPoint conversion).

**Docs:**
- Update the E2E checklist (README or existing E2E doc) with the manual auto-reply path.

---

## Task 1: Brain `replied` wire message

**Files:**
- Modify: `brain/src/glimpse_brain/protocol.py`
- Test: `brain/tests/test_protocol.py`

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_protocol.py` (and add `RepliedMsg` to the imports from `glimpse_brain.protocol`):

```python
def test_replied_roundtrip() -> None:
    # WHY: the shell reports each fill/send so the brain keeps an audit trail of
    # actions taken on real people. mode is a closed set — typos must fail loud.
    msg = RepliedMsg(suggestion_id="s1", region_id="region-1", mode="sent")
    parsed = parse_inbound(to_line(msg))
    assert isinstance(parsed, RepliedMsg)
    assert parsed.mode == "sent"


def test_replied_rejects_unknown_mode() -> None:
    with pytest.raises(ProtocolError):
        parse_inbound(
            '{"type":"replied","suggestion_id":"s1","region_id":"r","mode":"bogus"}'
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && python -m pytest tests/test_protocol.py::test_replied_roundtrip -v`
Expected: FAIL — `ImportError`/`NameError` on `RepliedMsg`.

- [ ] **Step 3: Write minimal implementation**

In `brain/src/glimpse_brain/protocol.py`, add the model after `ClickMsg`:

```python
class RepliedMsg(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["replied"] = "replied"
    suggestion_id: str
    region_id: str
    mode: Literal["fill", "sent", "cancelled"]
```

And extend the inbound union:

```python
InboundMsg = OcrMsg | HelloMsg | CopiedMsg | ClickMsg | SummarizeRequest | RepliedMsg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && python -m pytest tests/test_protocol.py -v`
Expected: PASS (all protocol tests).

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/protocol.py brain/tests/test_protocol.py
git commit -m "feat(brain): replied wire message for auto-reply audit trail"
```

---

## Task 2: Brain server logs `replied`

**Files:**
- Modify: `brain/src/glimpse_brain/server.py:117-120` (the `CopiedMsg` branch region)
- Test: `brain/tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_server.py`:

```python
async def test_replied_message_logged(tmp_path: Path) -> None:
    # WHY: a feature that sends messages to real customers must leave a record of
    # what was sent and how (fill/sent/cancelled). The audit line proves it.
    cfg = make_config(tmp_path)
    server = GlimpseServer(cfg, llm=FakeLLM())
    task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)
    try:
        _, writer = await asyncio.open_unix_connection(cfg.brain.socket_path)
        writer.write(
            b'{"type":"replied","suggestion_id":"s1","region_id":"region-1","mode":"sent"}\n'
        )
        await writer.drain()
        await asyncio.sleep(0.1)
        records = [
            json.loads(line)
            for line in Path(cfg.brain.event_log).read_text(encoding="utf-8").splitlines()
        ]
        replied = [r for r in records if r["kind"] == "replied"]
        assert len(replied) == 1
        assert replied[0]["payload"]["mode"] == "sent"
        assert replied[0]["payload"]["suggestion_id"] == "s1"
        writer.close()
    finally:
        task.cancel()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd brain && python -m pytest tests/test_server.py::test_replied_message_logged -v`
Expected: FAIL — no `replied` record (message ignored by dispatch).

- [ ] **Step 3: Write minimal implementation**

In `brain/src/glimpse_brain/server.py`, add `RepliedMsg` to the protocol imports (the block importing `CopiedMsg`, `ClickMsg`, …), then add a branch in `_dispatch` after the `CopiedMsg` branch (around line 120):

```python
        elif isinstance(msg, RepliedMsg):
            self._events.append(
                "replied",
                msg.region_id,
                {"suggestion_id": msg.suggestion_id, "mode": msg.mode},
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd brain && python -m pytest tests/test_server.py -v`
Expected: PASS (all server tests).

- [ ] **Step 5: Commit**

```bash
git add brain/src/glimpse_brain/server.py brain/tests/test_server.py
git commit -m "feat(brain): log replied events to the audit trail"
```

---

## Task 3: Swift `RepliedMsg` mirror + `ScreenCoords.cgPoint`

**Files:**
- Modify: `shell/Sources/GlimpseShellLib/Protocol.swift` (add `RepliedMsg` after `CopiedMsg`)
- Modify: `shell/Sources/GlimpseShellLib/RegionSelector.swift` (add `cgPoint`, make `KeyableWindow` internal)
- Test: `shell/Tests/GlimpseShellTests/ProtocolTests.swift`

- [ ] **Step 1: Write the failing test**

Add to `shell/Tests/GlimpseShellTests/ProtocolTests.swift`:

```swift
@Test
func repliedMsgEncodesSnakeCaseWire() throws {
    let data = try Wire.encodeLine(RepliedMsg(suggestionId: "s1", regionId: "region-1", mode: "sent"))
    let line = String(data: data, encoding: .utf8)!
    #expect(line.contains("\"type\":\"replied\""))
    #expect(line.contains("\"suggestion_id\":\"s1\""))
    #expect(line.contains("\"region_id\":\"region-1\""))
    #expect(line.contains("\"mode\":\"sent\""))
    #expect(line.hasSuffix("\n"))
}

@Test
func cgPointFlipsCocoaYToTopLeftOrigin() {
    // Primary screen height H: a Cocoa point (x, y) maps to CG (x, H - y).
    let h = NSScreen.screens.first?.frame.height ?? 0
    let cg = ScreenCoords.cgPoint(fromCocoa: CGPoint(x: 100, y: 100))
    #expect(cg.x == 100)
    #expect(cg.y == h - 100)
}
```

(Ensure the test file has `import AppKit` for `NSScreen`; add it if missing.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -20`
Expected: FAIL — `RepliedMsg` / `ScreenCoords.cgPoint` undefined.

- [ ] **Step 3: Write minimal implementation**

In `shell/Sources/GlimpseShellLib/Protocol.swift`, add after `CopiedMsg` (around line 71):

```swift
public struct RepliedMsg: Codable {
    public var type = "replied"
    public var suggestionId: String
    public var regionId: String
    public var mode: String

    public init(suggestionId: String, regionId: String, mode: String) {
        self.suggestionId = suggestionId
        self.regionId = regionId
        self.mode = mode
    }

    enum CodingKeys: String, CodingKey {
        case type
        case suggestionId = "suggestion_id"
        case regionId = "region_id"
        case mode
    }
}
```

In `shell/Sources/GlimpseShellLib/RegionSelector.swift`, add to the `ScreenCoords` enum:

```swift
    /// Cocoa global point (origin bottom-left) → CG global point (origin top-left).
    public static func cgPoint(fromCocoa p: CGPoint) -> CGPoint {
        let primaryHeight = NSScreen.screens.first?.frame.height ?? 0
        return CGPoint(x: p.x, y: primaryHeight - p.y)
    }
```

And change `private final class KeyableWindow: NSWindow {` to `final class KeyableWindow: NSWindow {` (drop `private`) so the calibrator can reuse it.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shell && swift test --filter ProtocolTests 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/Protocol.swift shell/Sources/GlimpseShellLib/RegionSelector.swift shell/Tests/GlimpseShellTests/ProtocolTests.swift
git commit -m "feat(shell): RepliedMsg mirror + cgPoint conversion helper"
```

---

## Task 4: `InputBoxStore`

**Files:**
- Create: `shell/Sources/GlimpseShellLib/InputBoxStore.swift`
- Test: `shell/Tests/GlimpseShellTests/InputBoxStoreTests.swift`

- [ ] **Step 1: Write the failing test**

Create `shell/Tests/GlimpseShellTests/InputBoxStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func inputBoxStoreRoundTripsPoint() throws {
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("ibx-\(UUID()).json")
    defer { try? FileManager.default.removeItem(at: tmp) }

    InputBoxStore.save(CGPoint(x: 640, y: 900), to: tmp)
    let loaded = InputBoxStore.load(from: tmp)
    #expect(loaded == CGPoint(x: 640, y: 900))
}

@Test
func inputBoxStoreFailsClosedOnMissingFile() {
    let missing = FileManager.default.temporaryDirectory
        .appendingPathComponent("nope-\(UUID()).json")
    #expect(InputBoxStore.load(from: missing) == nil)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shell && swift test --filter InputBoxStore 2>&1 | tail -20`
Expected: FAIL — `InputBoxStore` undefined.

- [ ] **Step 3: Write minimal implementation**

Create `shell/Sources/GlimpseShellLib/InputBoxStore.swift`:

```swift
import Foundation

/// Persists the calibrated chat input-box point (CG coordinates) across restarts.
/// Mirror of RegionStore. Default location: ~/.glimpse/input-box.json
public enum InputBoxStore {
    private struct Stored: Codable {
        var x: Double
        var y: Double
    }

    public static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/input-box.json")
    }

    public static func save(_ point: CGPoint, to url: URL = InputBoxStore.url) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let stored = Stored(x: point.x, y: point.y)
        try? JSONEncoder().encode(stored).write(to: url)
    }

    public static func load(from url: URL = InputBoxStore.url) -> CGPoint? {
        guard let data = try? Data(contentsOf: url),
            let stored = try? JSONDecoder().decode(Stored.self, from: data)
        else { return nil }
        return CGPoint(x: stored.x, y: stored.y)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shell && swift test --filter InputBoxStore 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/InputBoxStore.swift shell/Tests/GlimpseShellTests/InputBoxStoreTests.swift
git commit -m "feat(shell): InputBoxStore persists calibrated input-box point"
```

---

## Task 5: `SendPlanner` (pure safety core)

**Files:**
- Create: `shell/Sources/GlimpseShellLib/SendPlanner.swift`
- Test: `shell/Tests/GlimpseShellTests/SendPlannerTests.swift`

- [ ] **Step 1: Write the failing test**

Create `shell/Tests/GlimpseShellTests/SendPlannerTests.swift`:

```swift
import Testing
@testable import GlimpseShellLib

private func ctx(
    frontmostAllowed: Bool = true,
    calibrated: Bool = true,
    accessibilityTrusted: Bool = true,
    autoSendOn: Bool = false,
    stale: Bool = false
) -> SendContext {
    SendContext(
        frontmostAllowed: frontmostAllowed,
        calibrated: calibrated,
        accessibilityTrusted: accessibilityTrusted,
        autoSendOn: autoSendOn,
        stale: stale
    )
}

@Test
func refusesWhenAccessibilityUntrusted() {
    // Can't post any synthetic event without Accessibility trust.
    #expect(SendPlanner.plan(ctx(accessibilityTrusted: false)) == .refuse(.accessibilityUntrusted))
}

@Test
func refusesWhenNotCalibrated() {
    // No calibrated point ⇒ clicking would target a meaningless location.
    #expect(SendPlanner.plan(ctx(calibrated: false)) == .refuse(.notCalibrated))
}

@Test
func refusesWhenWrongAppFrontmost() {
    // The disaster guard: never paste/send unless the chat app owns the screen.
    #expect(SendPlanner.plan(ctx(frontmostAllowed: false)) == .refuse(.wrongApp))
}

@Test
func fillsWhenAutoSendOff() {
    #expect(SendPlanner.plan(ctx(autoSendOn: false)) == .fill)
}

@Test
func staleDowngradesAutoSendToFill() {
    // The stale-block: never auto-send a reply the conversation has outrun.
    #expect(SendPlanner.plan(ctx(autoSendOn: true, stale: true)) == .fill)
}

@Test
func sendsOnlyWhenAutoSendOnAndNotStaleAndChecksPass() {
    #expect(SendPlanner.plan(ctx(autoSendOn: true, stale: false)) == .fillThenSend)
}

@Test
func refuseReasonsCarryUserFacingMessages() {
    #expect(RefuseReason.wrongApp.message == "切换到微信再发送")
    #expect(RefuseReason.notCalibrated.message == "先设置输入框位置")
    #expect(RefuseReason.accessibilityUntrusted.message == "需要辅助功能权限")
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shell && swift test --filter SendPlanner 2>&1 | tail -20`
Expected: FAIL — `SendPlanner` / `SendContext` / `SendPlan` / `RefuseReason` undefined.

- [ ] **Step 3: Write minimal implementation**

Create `shell/Sources/GlimpseShellLib/SendPlanner.swift`:

```swift
import Foundation

/// Why a send was refused; carries the message surfaced in the overlay status.
public enum RefuseReason: Equatable {
    case wrongApp
    case notCalibrated
    case accessibilityUntrusted

    public var message: String {
        switch self {
        case .wrongApp: return "切换到微信再发送"
        case .notCalibrated: return "先设置输入框位置"
        case .accessibilityUntrusted: return "需要辅助功能权限"
        }
    }
}

/// The decision: do nothing, fill the box, or fill then send.
public enum SendPlan: Equatable {
    case refuse(RefuseReason)
    case fill
    case fillThenSend
}

/// All inputs to the decision — pure data, no OS coupling.
public struct SendContext {
    public let frontmostAllowed: Bool
    public let calibrated: Bool
    public let accessibilityTrusted: Bool
    public let autoSendOn: Bool
    public let stale: Bool

    public init(
        frontmostAllowed: Bool,
        calibrated: Bool,
        accessibilityTrusted: Bool,
        autoSendOn: Bool,
        stale: Bool
    ) {
        self.frontmostAllowed = frontmostAllowed
        self.calibrated = calibrated
        self.accessibilityTrusted = accessibilityTrusted
        self.autoSendOn = autoSendOn
        self.stale = stale
    }
}

/// The single home of every safety decision. Fail-closed: any failed
/// precondition refuses before any synthetic event is posted.
public enum SendPlanner {
    public static func plan(_ ctx: SendContext) -> SendPlan {
        if !ctx.accessibilityTrusted { return .refuse(.accessibilityUntrusted) }
        if !ctx.calibrated { return .refuse(.notCalibrated) }
        if !ctx.frontmostAllowed { return .refuse(.wrongApp) }
        if ctx.autoSendOn && !ctx.stale { return .fillThenSend }
        return .fill
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shell && swift test --filter SendPlanner 2>&1 | tail -20`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/SendPlanner.swift shell/Tests/GlimpseShellTests/SendPlannerTests.swift
git commit -m "feat(shell): SendPlanner — pure fail-closed send decision"
```

---

## Task 6: `Countdown` (externally-ticked, cancellable)

**Files:**
- Create: `shell/Sources/GlimpseShellLib/Countdown.swift`
- Test: `shell/Tests/GlimpseShellTests/CountdownTests.swift`

- [ ] **Step 1: Write the failing test**

Create `shell/Tests/GlimpseShellTests/CountdownTests.swift`:

```swift
import Testing
@testable import GlimpseShellLib

@Test
func countdownFiresOnceAfterEnoughTicks() {
    // 5 ticks (one per second) reach zero and fire the send exactly once.
    var fired = 0
    let cd = Countdown(seconds: 5) { fired += 1 }
    for _ in 0..<5 { cd.tick() }
    #expect(fired == 1)
    #expect(cd.isFinished)
}

@Test
func extraTicksAfterCompletionDoNotRefire() {
    var fired = 0
    let cd = Countdown(seconds: 3) { fired += 1 }
    for _ in 0..<10 { cd.tick() }
    #expect(fired == 1)
}

@Test
func cancelBeforeCompletionPreventsFiring() {
    // The abort window must be real: a cancel before zero never sends.
    var fired = 0
    let cd = Countdown(seconds: 5) { fired += 1 }
    cd.tick()
    cd.tick()
    cd.cancel()
    cd.tick()
    cd.tick()
    cd.tick()
    #expect(fired == 0)
    #expect(cd.isFinished)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shell && swift test --filter Countdown 2>&1 | tail -20`
Expected: FAIL — `Countdown` undefined.

- [ ] **Step 3: Write minimal implementation**

Create `shell/Sources/GlimpseShellLib/Countdown.swift`:

```swift
import Foundation

/// A countdown ticked externally (one tick per second by the UI timer), so the
/// abort logic is testable without real time. Fires `onComplete` exactly once
/// when it reaches zero, unless cancelled first.
public final class Countdown {
    public static let defaultSeconds = 5

    public private(set) var remaining: Int
    private let onComplete: () -> Void
    private var done = false

    public init(seconds: Int = Countdown.defaultSeconds, onComplete: @escaping () -> Void) {
        self.remaining = seconds
        self.onComplete = onComplete
    }

    /// Whether the countdown has resolved (completed or cancelled).
    public var isFinished: Bool { done }

    public func tick() {
        guard !done else { return }
        remaining -= 1
        if remaining <= 0 {
            done = true
            onComplete()
        }
    }

    public func cancel() {
        guard !done else { return }
        done = true
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shell && swift test --filter Countdown 2>&1 | tail -20`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/Countdown.swift shell/Tests/GlimpseShellTests/CountdownTests.swift
git commit -m "feat(shell): Countdown — externally-ticked cancellable timer"
```

---

## Task 7: `SyntheticInput` protocol + `CGEvent` implementation

**Files:**
- Create: `shell/Sources/GlimpseShellLib/SyntheticInput.swift`

This is the deliberate manual-tested seam (OS event injection can't be meaningfully
unit-tested). No failing-test-first; it's a protocol plus a thin leaf used by `Sender`
(Task 8), whose tests inject a mock. Verify it compiles.

- [ ] **Step 1: Create the protocol and implementation**

Create `shell/Sources/GlimpseShellLib/SyntheticInput.swift`:

```swift
import CoreGraphics

/// The Computer-Use primitive: the only unit that posts OS input events.
/// Injected as a protocol so Sender's logic is testable with a mock.
public protocol SyntheticInput {
    /// Click at a CG global point (top-left origin) — focuses the field and
    /// confirms which window receives subsequent input.
    func click(at point: CGPoint)
    /// Paste the current pasteboard contents (⌘V).
    func paste()
    /// Press Return — the irreversible send in auto-send mode.
    func pressReturn()
}

/// Production implementation backed by CGEvent. Requires Accessibility trust,
/// which the app already holds for click-tracking.
public final class CGEventSyntheticInput: SyntheticInput {
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
```

- [ ] **Step 2: Verify it compiles**

Run: `cd shell && swift build 2>&1 | tail -20`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add shell/Sources/GlimpseShellLib/SyntheticInput.swift
git commit -m "feat(shell): SyntheticInput protocol + CGEvent implementation"
```

---

## Task 8: `Sender` (orchestrator)

**Files:**
- Create: `shell/Sources/GlimpseShellLib/Sender.swift`
- Test: `shell/Tests/GlimpseShellTests/SenderTests.swift`

- [ ] **Step 1: Write the failing test**

Create `shell/Tests/GlimpseShellTests/SenderTests.swift`:

```swift
import CoreGraphics
import Testing
@testable import GlimpseShellLib

private final class MockSyntheticInput: SyntheticInput {
    var clicks = 0
    var pastes = 0
    var returns = 0
    func click(at point: CGPoint) { clicks += 1 }
    func paste() { pastes += 1 }
    func pressReturn() { returns += 1 }
}

/// Builds a Sender with controllable dependencies and captures its outputs.
private final class Harness {
    let mock = MockSyntheticInput()
    var frontmostAllowed = true
    var capturedCountdown: Countdown?
    var replied: [(String, String)] = []
    var refusals: [RefuseReason] = []
    var pasteboard = ""
    lazy var sender = Sender(
        synthetic: mock,
        isFrontmostAllowed: { self.frontmostAllowed },
        inputBoxPoint: { CGPoint(x: 100, y: 200) },
        accessibilityTrusted: { true },
        setPasteboard: { self.pasteboard = $0 },
        presentRefusal: { self.refusals.append($0) },
        presentCountdown: { self.capturedCountdown = $0 },
        emitReplied: { id, mode in self.replied.append((id, mode)) }
    )
}

@Test
func fillPlanPastesAndNeverPressesReturn() {
    let h = Harness()
    h.sender.handle(suggestionId: "s1", text: "你好", autoSendOn: false, stale: false)
    #expect(h.mock.clicks == 1)
    #expect(h.mock.pastes == 1)
    #expect(h.mock.returns == 0)
    #expect(h.pasteboard == "你好")
    #expect(h.replied.count == 1 && h.replied[0] == ("s1", "fill"))
}

@Test
func autoSendPressesReturnOnceWhenCountdownCompletesAndAppStillFrontmost() {
    let h = Harness()
    h.sender.handle(suggestionId: "s2", text: "好的", autoSendOn: true, stale: false)
    #expect(h.mock.pastes == 1)        // filled immediately
    #expect(h.mock.returns == 0)       // not yet
    let cd = try! #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 1)
    #expect(h.replied.contains { $0 == ("s2", "sent") })
}

@Test
func cancelDuringCountdownNeverSends() {
    let h = Harness()
    h.sender.handle(suggestionId: "s3", text: "稍等", autoSendOn: true, stale: false)
    h.sender.cancelPendingSend()
    let cd = try! #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s3", "cancelled") })
}

@Test
func appLeavingFrontmostAtSendTimeAborts() {
    // The race the countdown opens: user ⌘-Tabs away before it elapses.
    let h = Harness()
    h.sender.handle(suggestionId: "s4", text: "好的", autoSendOn: true, stale: false)
    h.frontmostAllowed = false        // user switched apps during the countdown
    let cd = try! #require(h.capturedCountdown)
    for _ in 0..<Countdown.defaultSeconds { cd.tick() }
    #expect(h.mock.returns == 0)
    #expect(h.replied.contains { $0 == ("s4", "cancelled") })
}

@Test
func refusalPostsNoEventsAndSurfacesReason() {
    let h = Harness()
    h.frontmostAllowed = false
    h.sender.handle(suggestionId: "s5", text: "x", autoSendOn: false, stale: false)
    #expect(h.mock.clicks == 0 && h.mock.pastes == 0 && h.mock.returns == 0)
    #expect(h.refusals == [.wrongApp])
    #expect(h.replied.isEmpty)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd shell && swift test --filter Sender 2>&1 | tail -20`
Expected: FAIL — `Sender` undefined.

- [ ] **Step 3: Write minimal implementation**

Create `shell/Sources/GlimpseShellLib/Sender.swift`:

```swift
import CoreGraphics

/// Orchestrates a fill/send: asks SendPlanner, then drives SyntheticInput, the
/// Countdown, and the audit callback. All OS access is injected so the logic is
/// testable. Main-thread use only (UI-driven).
public final class Sender {
    private let synthetic: SyntheticInput
    private let isFrontmostAllowed: () -> Bool
    private let inputBoxPoint: () -> CGPoint?
    private let accessibilityTrusted: () -> Bool
    private let setPasteboard: (String) -> Void
    private let presentRefusal: (RefuseReason) -> Void
    private let presentCountdown: (Countdown) -> Void
    private let emitReplied: (String, String) -> Void
    private let countdownSeconds: Int

    private var pending: (countdown: Countdown, suggestionId: String)?

    public init(
        synthetic: SyntheticInput,
        isFrontmostAllowed: @escaping () -> Bool,
        inputBoxPoint: @escaping () -> CGPoint?,
        accessibilityTrusted: @escaping () -> Bool,
        setPasteboard: @escaping (String) -> Void,
        presentRefusal: @escaping (RefuseReason) -> Void,
        presentCountdown: @escaping (Countdown) -> Void,
        emitReplied: @escaping (_ suggestionId: String, _ mode: String) -> Void,
        countdownSeconds: Int = Countdown.defaultSeconds
    ) {
        self.synthetic = synthetic
        self.isFrontmostAllowed = isFrontmostAllowed
        self.inputBoxPoint = inputBoxPoint
        self.accessibilityTrusted = accessibilityTrusted
        self.setPasteboard = setPasteboard
        self.presentRefusal = presentRefusal
        self.presentCountdown = presentCountdown
        self.emitReplied = emitReplied
        self.countdownSeconds = countdownSeconds
    }

    public func handle(suggestionId: String, text: String, autoSendOn: Bool, stale: Bool) {
        let context = SendContext(
            frontmostAllowed: isFrontmostAllowed(),
            calibrated: inputBoxPoint() != nil,
            accessibilityTrusted: accessibilityTrusted(),
            autoSendOn: autoSendOn,
            stale: stale
        )
        switch SendPlanner.plan(context) {
        case .refuse(let reason):
            presentRefusal(reason)
        case .fill:
            fill(text)
            emitReplied(suggestionId, "fill")
        case .fillThenSend:
            fill(text)
            let countdown = Countdown(seconds: countdownSeconds) { [weak self] in
                self?.finalizeSend(suggestionId)
            }
            pending = (countdown, suggestionId)
            presentCountdown(countdown)
        }
    }

    /// Cancel a pending auto-send (Esc, the kill-switch, or app-switch).
    public func cancelPendingSend() {
        guard let pending else { return }
        pending.countdown.cancel()
        emitReplied(pending.suggestionId, "cancelled")
        self.pending = nil
    }

    private func fill(_ text: String) {
        guard let point = inputBoxPoint() else { return }  // already verified in plan
        setPasteboard(text)
        synthetic.click(at: point)
        synthetic.paste()
    }

    private func finalizeSend(_ suggestionId: String) {
        defer { pending = nil }
        // Re-check at send time: the user may have ⌘-Tabbed away during the
        // countdown. Never press Return into whatever else now owns the screen.
        guard isFrontmostAllowed() else {
            emitReplied(suggestionId, "cancelled")
            return
        }
        synthetic.pressReturn()
        emitReplied(suggestionId, "sent")
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd shell && swift test --filter Sender 2>&1 | tail -20`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/Sender.swift shell/Tests/GlimpseShellTests/SenderTests.swift
git commit -m "feat(shell): Sender orchestrates fill/auto-send with guards"
```

---

## Task 9: `InputBoxCalibrator` (single-click calibration UI)

**Files:**
- Create: `shell/Sources/GlimpseShellLib/InputBoxCalibrator.swift`

UI unit — verified via the manual E2E checklist (Task 12), not a unit test. Mirrors
`RegionSelector`'s window/coordinate handling, reusing `KeyableWindow` and
`ScreenCoords.cgPoint` (made available in Task 3).

- [ ] **Step 1: Create the calibrator**

Create `shell/Sources/GlimpseShellLib/InputBoxCalibrator.swift`:

```swift
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
```

- [ ] **Step 2: Verify it compiles**

Run: `cd shell && swift build 2>&1 | tail -20`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add shell/Sources/GlimpseShellLib/InputBoxCalibrator.swift
git commit -m "feat(shell): InputBoxCalibrator single-click calibration UI"
```

---

## Task 10: Overlay — fill/send button + countdown banner

**Files:**
- Modify: `shell/Sources/GlimpseShellLib/Overlay.swift`

UI change — verified via the manual E2E checklist. Adds `autoSendOn` and
`countdownRemaining` to the model, an `onAct` callback, the fill/send button, and a
countdown banner. The existing `复制` button and `onCopy` stay unchanged.

- [ ] **Step 1: Extend `OverlayModel`**

In `OverlayModel`, add published state and the action callback (after `var summary`):

```swift
    @Published public var autoSendOn = false
    @Published public var countdownRemaining: Int?
    public var onAct: ((_ suggestionId: String, _ text: String) -> Void)?
```

- [ ] **Step 2: Add controller methods for the countdown banner**

In `OverlayController`, add:

```swift
    /// Safe to call from any thread: @Published mutation hops to main inside.
    public func showCountdown(remaining: Int) {
        DispatchQueue.main.async { self.model.countdownRemaining = remaining }
    }

    public func hideCountdown() {
        DispatchQueue.main.async { self.model.countdownRemaining = nil }
    }

    public func setAutoSend(_ on: Bool) {
        DispatchQueue.main.async { self.model.autoSendOn = on }
    }
```

- [ ] **Step 3: Render the countdown banner and the fill/send button**

In `OverlayView.body`, add the banner just below the status `HStack` (before the summary block):

```swift
            if let remaining = model.countdownRemaining {
                Text("发送中 \(remaining)…  按 Esc 取消")
                    .font(.system(size: 13)).bold()
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(8)
                    .background(Color.red.opacity(0.85))
                    .cornerRadius(6)
            }
```

In the `ForEach(model.items)` card `HStack`, add the new button after the existing
`Button("复制")`:

```swift
                            Button(model.autoSendOn && !model.stale ? "发送" : "填入") {
                                model.onAct?(item.id, item.text)
                            }
```

- [ ] **Step 4: Verify it compiles**

Run: `cd shell && swift build 2>&1 | tail -20`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add shell/Sources/GlimpseShellLib/Overlay.swift
git commit -m "feat(shell): overlay fill/send button + auto-send countdown banner"
```

---

## Task 11: `main.swift` — menu, toggle, Sender wiring, Esc/tick driving

**Files:**
- Modify: `shell/Sources/GlimpseShell/main.swift`

Wires everything: the calibration menu item, the off-by-default auto-send toggle
(persisted in `UserDefaults`, also the kill-switch), the `Sender`, the overlay
`onAct`, and the countdown's per-second tick + Esc monitors.

- [ ] **Step 1: Add stored properties**

In `AppDelegate`, add alongside the other `private var`s:

```swift
    private var sender: Sender!
    private var calibrator: InputBoxCalibrator?
    private var countdownTimer: Timer?
    private var escMonitors: [Any] = []
    private var autoSendEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: "autoSendEnabled") }
        set { UserDefaults.standard.set(newValue, forKey: "autoSendEnabled") }
    }
```

- [ ] **Step 2: Add the menu items**

In `applicationDidFinishLaunching`, after the "Today's Interests" item, add:

```swift
        menu.addItem(
            NSMenuItem(title: "设置输入框位置", action: #selector(calibrateInputBox), keyEquivalent: "i")
        )
        let autoSendItem = NSMenuItem(
            title: "自动发送", action: #selector(toggleAutoSend(_:)), keyEquivalent: ""
        )
        autoSendItem.state = autoSendEnabled ? .on : .off
        menu.addItem(autoSendItem)
```

(`menu.items.forEach { $0.target = self }` already runs afterward, so these get the target.)

- [ ] **Step 3: Build the `Sender` and wire the overlay action**

In `applicationDidFinishLaunching`, after `overlay.model.onCopy = …` and before
`overlay.show()`, add:

```swift
        let sendAllowlist = AppAllowlist(path: AppAllowlist.defaultPath)
        sender = Sender(
            synthetic: CGEventSyntheticInput(),
            isFrontmostAllowed: {
                sendAllowlist.isAllowed(NSWorkspace.shared.frontmostApplication?.bundleIdentifier)
            },
            inputBoxPoint: { InputBoxStore.load() },
            accessibilityTrusted: { AXIsProcessTrusted() },
            setPasteboard: { text in
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            },
            presentRefusal: { [weak self] reason in
                self?.overlay.setStatus("degraded", detail: reason.message)
            },
            presentCountdown: { [weak self] countdown in
                self?.driveCountdown(countdown)
            },
            emitReplied: { [weak self] id, mode in
                guard let self else { return }
                self.ipc.send(RepliedMsg(suggestionId: id, regionId: self.regionId, mode: mode))
            }
        )

        overlay.model.onAct = { [weak self] id, text in
            guard let self else { return }
            self.sender.handle(
                suggestionId: id, text: text,
                autoSendOn: self.autoSendEnabled, stale: self.overlay.model.stale
            )
        }
        overlay.setAutoSend(autoSendEnabled)
```

(`import AppKit` already present; `AXIsProcessTrusted` is from `ApplicationServices`,
re-exported by AppKit — if the build complains, add `import ApplicationServices`.)

- [ ] **Step 4: Add the action methods and the countdown driver**

Add these methods to `AppDelegate`:

```swift
    @objc private func calibrateInputBox() {
        calibrator = InputBoxCalibrator { [weak self] point in
            InputBoxStore.save(point)
            self?.calibrator = nil
            self?.overlay.setStatus("watching", detail: "输入框已设置")
        }
        calibrator?.begin()
    }

    @objc private func toggleAutoSend(_ item: NSMenuItem) {
        autoSendEnabled.toggle()
        item.state = autoSendEnabled ? .on : .off
        overlay.setAutoSend(autoSendEnabled)
    }

    /// Ticks the countdown once per second, mirrors it into the overlay banner,
    /// and lets Esc cancel it. Re-check of the frontmost app happens inside the
    /// countdown's completion (Sender.finalizeSend).
    private func driveCountdown(_ countdown: Countdown) {
        overlay.showCountdown(remaining: countdown.remaining)
        let local = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 { self?.sender.cancelPendingSend(); return nil }
            return event
        }
        let global = NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 { self?.sender.cancelPendingSend() }
        }
        escMonitors = [local, global].compactMap { $0 }

        countdownTimer?.invalidate()
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] timer in
            guard let self else { timer.invalidate(); return }
            countdown.tick()
            self.overlay.showCountdown(remaining: max(countdown.remaining, 0))
            if countdown.isFinished {
                timer.invalidate()
                self.overlay.hideCountdown()
                self.escMonitors.forEach { NSEvent.removeMonitor($0) }
                self.escMonitors = []
            }
        }
    }
```

- [ ] **Step 5: Build and run the full test suite**

Run: `cd shell && swift build 2>&1 | tail -20 && swift test 2>&1 | tail -20`
Expected: build succeeds; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add shell/Sources/GlimpseShell/main.swift
git commit -m "feat(shell): wire calibration, auto-send toggle, Sender, countdown"
```

---

## Task 12: Manual E2E checklist + allowlist note

**Files:**
- Modify: `README.md` (or the existing E2E checklist doc — search for the Phase 2 checklist and add a Phase 3 section)

- [ ] **Step 1: Document the calibration + allowlist prerequisite and the E2E path**

Add a "Phase 3 — Auto-Reply" section to the E2E checklist with these steps:

```markdown
### Phase 3 — Auto-Reply (manual E2E)

Prerequisites:
- WeChat's bundle ID is in `~/.glimpse/allowlist.json` (same list as click-capture).
- Accessibility permission granted (same as click-tracking).

1. Menu → **设置输入框位置**, click WeChat's message input box. Status shows "输入框已设置".
2. With auto-send OFF (default): pick a suggestion, click **填入**.
   - Expect: the reply text appears in WeChat's input box; nothing is sent.
3. Wrong-app refusal: bring another app to the front, click **填入**.
   - Expect: status shows "切换到微信再发送"; nothing is typed.
4. Turn ON menu → **自动发送**. The card button now reads **发送** (unless the
   suggestion is stale, where it stays **填入**).
5. Click **发送** with WeChat frontmost.
   - Expect: text fills, a red "发送中 5…4…3…2…1 按 Esc 取消" banner counts down,
     then Return is pressed and the message sends.
6. Repeat step 5 but press **Esc** during the countdown.
   - Expect: countdown aborts, message NOT sent, text left in the box.
7. Repeat step 5 but ⌘-Tab away before the countdown ends.
   - Expect: at zero, the send aborts (frontmost re-check); message NOT sent.
8. Confirm `~/.glimpse/events.jsonl` has `replied` records with modes
   `fill` / `sent` / `cancelled` matching the actions above.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: Phase 3 auto-reply manual E2E checklist"
```

---

## Self-Review

**Spec coverage:**
- Fill-only default + opt-in toggle → Tasks 5 (`SendPlanner`), 10 (button), 11 (toggle). ✓
- Paste via ⌘V → Task 7 (`SyntheticInput.paste`). ✓
- Calibrated click-point → Tasks 4 (`InputBoxStore`), 9 (`InputBoxCalibrator`), 8 (`Sender.fill` clicks then pastes). ✓
- Fail-closed frontmost-app check (Approach B) → Task 5 (`wrongApp`), Task 11 (`isFrontmostAllowed` via `AppAllowlist`). ✓
- 5s Esc-cancellable countdown → Tasks 6 (`Countdown`), 8 (wiring), 10 (banner), 11 (tick + Esc). ✓
- Stale-block → Task 5 (`staleDowngradesAutoSendToFill`), Task 10 (button stays 填入). ✓
- Re-check frontmost at Return time → Task 8 (`finalizeSend` + `appLeavingFrontmostAtSendTimeAborts` test). ✓
- Kill-switch = toggle → Task 11 (`toggleAutoSend`). ✓
- Audit log via `replied` → Tasks 1, 2 (brain), 3 (Swift mirror), 8/11 (`emitReplied`). ✓
- Accessibility-untrusted refusal → Task 5 (`refusesWhenAccessibilityUntrusted`), Task 11 (`AXIsProcessTrusted`). ✓
- Testing strategy (pure units tested; `SyntheticInput` manual seam) → Tasks 5,6,4,8 unit-tested; Tasks 7,9,10,11 covered by Task 12 E2E. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `SendContext` / `SendPlan` / `RefuseReason` (Task 5) used identically in Tasks 8 and 11. `Countdown(seconds:onComplete:)` + `tick()`/`cancel()`/`remaining`/`isFinished`/`defaultSeconds` (Task 6) used consistently in Tasks 8, 11, and the Sender tests. `Sender.init` signature (Task 8) matches its construction in Task 11. `SyntheticInput` methods `click(at:)`/`paste()`/`pressReturn()` (Task 7) match the mock and `Sender` (Task 8). `RepliedMsg(suggestionId:regionId:mode:)` (Task 3) matches `emitReplied` use (Task 11) and the brain model fields (Task 1). `InputBoxStore.save/load(_:to:/from:)` (Task 4) match Task 11 usage (default-argument forms). `ScreenCoords.cgPoint` + `KeyableWindow` (Task 3) used by Task 9. ✓

No gaps found.
