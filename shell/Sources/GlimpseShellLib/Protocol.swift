import Foundation

// MARK: - Messages (mirror of brain/src/glimpse_brain/protocol.py)

public struct Block: Codable, Equatable {
    public var text: String
    public var x0: Double
    public var x1: Double
    public var conf: Double
    // Normalized [0,1], top-left origin. Defaults keep pre-P7.2 call sites
    // valid; OCR fills real values so click→block mapping is computable.
    public var y0: Double
    public var y1: Double

    public init(text: String, x0: Double, x1: Double, conf: Double, y0: Double = 0, y1: Double = 0) {
        self.text = text
        self.x0 = x0
        self.x1 = x1
        self.conf = conf
        self.y0 = y0
        self.y1 = y1
    }
}

public struct OcrMsg: Codable {
    public var type = "ocr"
    public var seq: Int
    public var ts: String
    public var regionId: String
    public var blocks: [Block]
    public var contact: String
    public var image: String

    public init(seq: Int, ts: String, regionId: String, blocks: [Block], contact: String = "", image: String = "") {
        self.seq = seq
        self.ts = ts
        self.regionId = regionId
        self.blocks = blocks
        self.contact = contact
        self.image = image
    }

    enum CodingKeys: String, CodingKey {
        case type, seq, ts
        case regionId = "region_id"
        case blocks, contact, image
    }
}

public struct HelloMsg: Codable {
    public var type = "hello"
    public var shellVersion: String

    public init(shellVersion: String) {
        self.shellVersion = shellVersion
    }

    enum CodingKeys: String, CodingKey {
        case type
        case shellVersion = "shell_version"
    }
}

public struct CopiedMsg: Codable {
    public var type = "copied"
    public var suggestionId: String
    public var regionId: String

    public init(suggestionId: String, regionId: String) {
        self.suggestionId = suggestionId
        self.regionId = regionId
    }

    enum CodingKeys: String, CodingKey {
        case type
        case suggestionId = "suggestion_id"
        case regionId = "region_id"
    }
}

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

public struct FeedbackMsg: Codable {
    public var type = "feedback"
    public var suggestionId: String
    public var regionId: String
    public var verdict: String
    public var note: String

    public init(suggestionId: String, regionId: String, verdict: String, note: String = "") {
        self.suggestionId = suggestionId
        self.regionId = regionId
        self.verdict = verdict
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case type
        case suggestionId = "suggestion_id"
        case regionId = "region_id"
        case verdict, note
    }
}

/// Operator's 开始/结束选品 control — a ground-truth trajectory boundary. The
/// brain's Sessionizer derives the session_id (audit §三 B3).
public struct SelectionControlMsg: Codable {
    public var type = "selection_control"
    public var ts: String
    public var action: String  // "start" | "end"

    public init(ts: String, action: String) {
        self.ts = ts
        self.action = action
    }

    enum CodingKeys: String, CodingKey {
        case type, ts, action
    }
}

/// The final selection result for the active trajectory — the flywheel's target
/// variable (audit §三 B4). `note` is where the implicit WHY gets captured.
public struct SelectionOutcomeMsg: Codable {
    public var type = "selection_outcome"
    public var ts: String
    public var productKey: String
    public var verdict: String  // "selected" | "rejected" | "shortlisted"
    public var note: String

    public init(ts: String, productKey: String = "", verdict: String, note: String = "") {
        self.ts = ts
        self.productKey = productKey
        self.verdict = verdict
        self.note = note
    }

    enum CodingKeys: String, CodingKey {
        case type, ts
        case productKey = "product_key"
        case verdict, note
    }
}

public struct ClickMsg: Codable {
    public var type = "click"
    public var ts: String
    public var app: String
    public var x: Double
    public var y: Double
    public var blocks: [Block]
    public var windowTitle: String
    public var url: String
    // false = metadata-only click (burst coalescing or snapshot/OCR failure):
    // the click FACT survives even when its screenshot doesn't.
    public var captureOk: Bool

    public init(
        ts: String, app: String, x: Double, y: Double, blocks: [Block],
        windowTitle: String = "", url: String = "", captureOk: Bool = true
    ) {
        self.ts = ts
        self.app = app
        self.x = x
        self.y = y
        self.blocks = blocks
        self.windowTitle = windowTitle
        self.url = url
        self.captureOk = captureOk
    }

    enum CodingKeys: String, CodingKey {
        case type, ts, app, x, y, blocks
        case windowTitle = "window_title"
        case url
        case captureOk = "capture_ok"
    }
}

/// A closed attention interval on one window/page — the dwell signal.
public struct DwellMsg: Codable {
    public var type = "dwell"
    public var app: String
    public var windowTitle: String
    public var url: String
    public var startTs: String
    public var endTs: String
    public var seconds: Double

    public init(
        app: String, windowTitle: String = "", url: String = "",
        startTs: String, endTs: String, seconds: Double
    ) {
        self.app = app
        self.windowTitle = windowTitle
        self.url = url
        self.startTs = startTs
        self.endTs = endTs
        self.seconds = seconds
    }

    enum CodingKeys: String, CodingKey {
        case type, app, url, seconds
        case windowTitle = "window_title"
        case startTs = "start_ts"
        case endTs = "end_ts"
    }
}

public struct SummarizeRequest: Codable {
    public var type = "summarize"

    public init() {}

    enum CodingKeys: String, CodingKey {
        case type
    }
}

public struct AdvisoryMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var text: String

    public init(text: String) {
        self.text = text
    }
}

public struct AckMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var seq: Int

    public init(seq: Int) {
        self.seq = seq
    }
}

public struct SuggestionItem: Codable, Identifiable, Equatable {
    public var id: String
    public var text: String

    public init(id: String, text: String) {
        self.id = id
        self.text = text
    }
}

public struct SuggestionsMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var regionId: String
    public var items: [SuggestionItem]
    public var stale: Bool

    public init(regionId: String, items: [SuggestionItem], stale: Bool) {
        self.regionId = regionId
        self.items = items
        self.stale = stale
    }

    enum CodingKeys: String, CodingKey {
        case regionId = "region_id"
        case items
        case stale
    }
}

public struct StatusMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var state: String
    public var detail: String

    public init(state: String, detail: String) {
        self.state = state
        self.detail = detail
    }
}

public struct SummaryMsg: Codable {
    // Decode-only: the wire "type" key is consumed by Wire.decodeBrainMessage's probe.
    public var text: String

    public init(text: String) {
        self.text = text
    }
}

public enum BrainMessage {
    case ack(AckMsg)
    case suggestions(SuggestionsMsg)
    case status(StatusMsg)
    case summary(SummaryMsg)
    case advisory(AdvisoryMsg)
}

// MARK: - Wire encoding

public enum Wire {
    public static func encodeLine<T: Codable>(_ msg: T) throws -> Data {
        let encoder = JSONEncoder()
        var data = try encoder.encode(msg)
        data.append(0x0A)  // newline
        return data
    }

    public static func decodeBrainMessage(_ line: Data) -> BrainMessage? {
        let decoder = JSONDecoder()
        struct Probe: Decodable {
            let type: String
        }

        guard let probe = try? decoder.decode(Probe.self, from: line) else { return nil }

        switch probe.type {
        case "ack":
            return (try? decoder.decode(AckMsg.self, from: line)).map(BrainMessage.ack)
        case "suggestions":
            return (try? decoder.decode(SuggestionsMsg.self, from: line)).map(BrainMessage.suggestions)
        case "status":
            return (try? decoder.decode(StatusMsg.self, from: line)).map(BrainMessage.status)
        case "summary":
            return (try? decoder.decode(SummaryMsg.self, from: line)).map(BrainMessage.summary)
        case "advisory":
            return (try? decoder.decode(AdvisoryMsg.self, from: line)).map(BrainMessage.advisory)
        default:
            return nil
        }
    }
}

// MARK: - NDJSON framing

public final class LineBuffer {
    private var buffer = Data()

    public init() {}

    public func feed(_ chunk: Data) -> [Data] {
        buffer.append(chunk)
        var lines: [Data] = []
        while let newlineIndex = buffer.firstIndex(of: 0x0A) {
            var line = buffer.subdata(in: buffer.startIndex..<newlineIndex)
            // Strip trailing CR if present (CRLF tolerance)
            if line.last == 0x0D {
                line = line.subdata(in: line.startIndex..<line.index(before: line.endIndex))
            }
            // Skip empty lines
            if !line.isEmpty {
                lines.append(line)
            }
            buffer.removeSubrange(buffer.startIndex...newlineIndex)
        }
        return lines
    }
}
