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
