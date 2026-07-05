import Foundation

/// Pure outbound send state: FIFO queue for disconnect spooling plus a single
/// unacknowledged OCR slot (latest frame only — stale OCR is intentionally dropped).
public struct OutboundSpool {
    public static let maxQueue = 100

    private var queue: [Data] = []
    private var unackedOcr: Data?
    private var unackedSeq: Int?
    public private(set) var droppedCount = 0

    public init() {}

    /// Non-OCR payloads while disconnected; oldest dropped at capacity.
    public mutating func enqueue(_ data: Data) {
        if queue.count >= Self.maxQueue {
            queue.removeFirst()
            droppedCount += 1
        }
        queue.append(data)
    }

    /// Latest OCR frame awaiting brain ack — replaces any prior unacked OCR.
    public mutating func setUnacked(_ data: Data, seq: Int) {
        unackedOcr = data
        unackedSeq = seq
    }

    /// Clears the OCR slot only when `seq` matches the retained unacked frame.
    @discardableResult
    public mutating func onAck(_ seq: Int) -> Bool {
        guard unackedSeq == seq else { return false }
        unackedOcr = nil
        unackedSeq = nil
        return true
    }

    /// On reconnect: unacked OCR first (if any), then queued messages FIFO; queue cleared.
    public mutating func drainOnConnect() -> [Data] {
        var out: [Data] = []
        if let ocr = unackedOcr {
            out.append(ocr)
        }
        out.append(contentsOf: queue)
        queue.removeAll()
        return out
    }
}
