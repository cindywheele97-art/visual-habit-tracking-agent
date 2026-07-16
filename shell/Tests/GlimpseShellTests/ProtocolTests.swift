import AppKit
import Foundation
import Testing
@testable import GlimpseShellLib

// WHY: field names on the wire must match the Python brain exactly
// (snake_case). A silent mismatch breaks the whole pipeline.
@Test
func ocrMsgEncodesSnakeCase() throws {
    let msg = OcrMsg(
        seq: 1, ts: "2026-06-11T12:00:00Z", regionId: "region-1",
        blocks: [Block(text: "你好", x0: 0.05, x1: 0.4, conf: 0.97)]
    )
    let data = try Wire.encodeLine(msg)
    let json = String(data: data, encoding: .utf8)!
    #expect(json.contains("\"region_id\":\"region-1\""))
    #expect(json.contains("\"type\":\"ocr\""))
    #expect(json.hasSuffix("\n"))
}

@Test
func clickMsgEncodesJoinKeyFieldsSnakeCase() throws {
    // WHY: window_title/url are the flywheel's JOIN keys and capture_ok is the
    // metadata-only marker — a key-name mismatch silently drops them at the
    // brain's extra="forbid" boundary.
    let msg = ClickMsg(
        ts: "t", app: "com.google.Chrome", x: 1, y: 2,
        blocks: [Block(text: "连衣裙", x0: 0.1, x1: 0.5, conf: 0.9, y0: 0.2, y1: 0.3)],
        windowTitle: "连衣裙批发 - 1688",
        url: "https://detail.1688.com/offer/612345678901.html",
        captureOk: false
    )
    let json = String(data: try Wire.encodeLine(msg), encoding: .utf8)!
    #expect(json.contains("\"window_title\":\"连衣裙批发 - 1688\""))
    // JSONEncoder escapes "/" as "\/" (legal JSON; the Python side decodes it
    // back) — assert on the key and an unescapable fragment, not raw slashes.
    #expect(json.contains("\"url\":"))
    #expect(json.contains("612345678901.html"))
    #expect(json.contains("\"capture_ok\":false"))
    #expect(json.contains("\"y0\":0.2"))
    #expect(json.contains("\"y1\":0.3"))
}

@Test
func dwellMsgEncodesSnakeCase() throws {
    let msg = DwellMsg(
        app: "com.apple.Safari", windowTitle: "淘宝", url: "https://item.taobao.com/item.htm?id=9",
        startTs: "2026-07-16T09:00:00Z", endTs: "2026-07-16T09:01:30Z", seconds: 90
    )
    let json = String(data: try Wire.encodeLine(msg), encoding: .utf8)!
    #expect(json.contains("\"type\":\"dwell\""))
    #expect(json.contains("\"start_ts\":\"2026-07-16T09:00:00Z\""))
    #expect(json.contains("\"end_ts\":\"2026-07-16T09:01:30Z\""))
    #expect(json.contains("\"seconds\":90"))
    #expect(json.contains("\"window_title\":\"淘宝\""))
}

@Test
func decodeSuggestionsFromBrain() throws {
    let line = #"{"type":"suggestions","region_id":"region-1","items":[{"id":"s1","text":"好的"}],"stale":false}"#
    guard case .suggestions(let msg)? = Wire.decodeBrainMessage(Data(line.utf8)) else {
        #expect(Bool(false), "expected suggestions")
        return
    }
    #expect(msg.items.first?.text == "好的")
}

@Test
func decodeAckAndStatus() throws {
    guard case .ack(let ack)? = Wire.decodeBrainMessage(Data(#"{"type":"ack","seq":7}"#.utf8)) else {
        #expect(Bool(false), "expected ack")
        return
    }
    #expect(ack.seq == 7)
    guard case .status(let st)? = Wire.decodeBrainMessage(
        Data(#"{"type":"status","state":"watching","detail":""}"#.utf8)
    ) else {
        #expect(Bool(false), "expected status")
        return
    }
    #expect(st.state == "watching")
}

@Test
func helloAndCopiedEncodeSnakeCase() throws {
    // WHY: these two inbound messages have the remaining snake_case fields;
    // a mapping regression would break the wire silently.
    let hello = String(data: try Wire.encodeLine(HelloMsg(shellVersion: "0.1.0")), encoding: .utf8)!
    #expect(hello.contains("\"shell_version\":\"0.1.0\""))
    let copied = String(
        data: try Wire.encodeLine(CopiedMsg(suggestionId: "s1", regionId: "region-1")),
        encoding: .utf8
    )!
    #expect(copied.contains("\"suggestion_id\":\"s1\""))
    #expect(copied.contains("\"region_id\":\"region-1\""))
}

@Test
func lineBufferStripsCRAndSkipsEmptyLines() {
    // WHY: a CRLF-emitting intermediary must not cause silent decode failures.
    let buf = LineBuffer()
    let lines = buf.feed(Data("{\"a\":1}\r\n\n{\"b\":2}\n".utf8))
    #expect(lines.count == 2)
    #expect(String(data: lines[0], encoding: .utf8) == "{\"a\":1}")
    #expect(String(data: lines[1], encoding: .utf8) == "{\"b\":2}")
}

@Test
func unknownTypeReturnsNil() {
    #expect(Wire.decodeBrainMessage(Data(#"{"type":"mystery"}"#.utf8)) == nil)
}

@Test
func lineBufferSplitsAndKeepsPartial() {
    let buf = LineBuffer()
    var lines = buf.feed(Data("{\"a\":1}\n{\"b\"".utf8))
    #expect(lines.count == 1)
    lines = buf.feed(Data(":2}\n".utf8))
    #expect(lines.count == 1)
    #expect(String(data: lines[0], encoding: .utf8) == "{\"b\":2}")
}

@Test
func clickMsgEncodesSnakeCaseAndType() throws {
    let msg = ClickMsg(
        ts: "2026-06-12T09:00:00Z", app: "com.google.Chrome", x: 12.0, y: 34.0,
        blocks: [Block(text: "Adidas", x0: 0.1, x1: 0.5, conf: 0.9)]
    )
    let json = String(data: try Wire.encodeLine(msg), encoding: .utf8)!
    #expect(json.contains("\"type\":\"click\""))
    #expect(json.contains("\"app\":\"com.google.Chrome\""))
    #expect(json.contains("\"text\":\"Adidas\""))
}

@Test
func summarizeRequestEncodesType() throws {
    let json = String(data: try Wire.encodeLine(SummarizeRequest()), encoding: .utf8)!
    #expect(json.contains("\"type\":\"summarize\""))
}

@Test
func decodeSummaryFromBrain() throws {
    let line = #"{"type":"summary","text":"今天你在看 Adidas"}"#
    guard case .summary(let msg)? = Wire.decodeBrainMessage(Data(line.utf8)) else {
        #expect(Bool(false), "expected summary")
        return
    }
    #expect(msg.text == "今天你在看 Adidas")
}

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
func ocrMsgEncodesContact() throws {
    let data = try Wire.encodeLine(
        OcrMsg(seq: 1, ts: "t", regionId: "r", blocks: [], contact: "小明")
    )
    let line = String(data: data, encoding: .utf8)!
    #expect(line.contains("\"contact\":\"小明\""))
}

@Test
func ocrMsgEncodesImage() throws {
    let data = try Wire.encodeLine(
        OcrMsg(seq: 1, ts: "t", regionId: "r", blocks: [], image: "QUJD")
    )
    let line = String(data: data, encoding: .utf8)!
    #expect(line.contains("\"image\":\"QUJD\""))
}

@Test
func cgPointFlipsCocoaYToTopLeftOrigin() {
    // Primary screen height H: a Cocoa point (x, y) maps to CG (x, H - y).
    let h = NSScreen.screens.first?.frame.height ?? 0
    let cg = ScreenCoords.cgPoint(fromCocoa: CGPoint(x: 100, y: 100))
    #expect(cg.x == 100)
    #expect(cg.y == h - 100)
}

@Test("FeedbackMsg encodes snake_case keys")
func feedbackMsgEncodesSnakeCase() throws {
    let data = try Wire.encodeLine(
        FeedbackMsg(suggestionId: "s1", regionId: "r", verdict: "down", note: "强调赠品")
    )
    let line = String(decoding: data, as: UTF8.self)
    #expect(line.contains("\"suggestion_id\":\"s1\""))
    #expect(line.contains("\"region_id\":\"r\""))
    #expect(line.contains("\"verdict\":\"down\""))
    #expect(line.contains("\"type\":\"feedback\""))
}

@Test("AdvisoryMsg decodes via Wire")
func advisoryMsgDecodes() throws {
    let line = Data("{\"type\":\"advisory\",\"text\":\"满意率已达标\"}".utf8)
    guard case .advisory(let msg)? = Wire.decodeBrainMessage(line) else {
        Issue.record("expected .advisory")
        return
    }
    #expect(msg.text == "满意率已达标")
}
