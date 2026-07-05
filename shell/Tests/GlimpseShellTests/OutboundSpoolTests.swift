import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func spoolQueuesWhileDisconnectedAndDrainsFIFOOnConnect() {
    // WHY: RepliedMsg / FeedbackMsg must survive a brain restart — not silently vanish.
    var spool = OutboundSpool()
    spool.enqueue(Data("first".utf8))
    spool.enqueue(Data("second".utf8))
    let drained = spool.drainOnConnect()
    #expect(drained.count == 2)
    #expect(String(data: drained[0], encoding: .utf8) == "first")
    #expect(String(data: drained[1], encoding: .utf8) == "second")
    #expect(spool.drainOnConnect().isEmpty)
}

@Test
func spoolCapsAtLimitDroppingOldest() {
    // WHY: an unbounded offline queue could exhaust memory during a long outage.
    var spool = OutboundSpool()
    for i in 0..<101 {
        spool.enqueue(Data("msg-\(i)".utf8))
    }
    #expect(spool.droppedCount == 1)
    let drained = spool.drainOnConnect()
    #expect(drained.count == 100)
    #expect(String(data: drained[0], encoding: .utf8) == "msg-1")
    #expect(String(data: drained[99], encoding: .utf8) == "msg-100")
}

@Test
func unackedOcrDrainsBeforeQueuedMessages() {
    // WHY: the brain must see the latest frame before audit/feedback lines — OCR is not FIFO.
    var spool = OutboundSpool()
    spool.setUnacked(Data("ocr-frame".utf8), seq: 7)
    spool.enqueue(Data("replied".utf8))
    let drained = spool.drainOnConnect()
    #expect(drained.count == 2)
    #expect(String(data: drained[0], encoding: .utf8) == "ocr-frame")
    #expect(String(data: drained[1], encoding: .utf8) == "replied")
}

@Test
func ackClearsTheSlotOnlyOnMatchingSeq() {
    // WHY: a stale ack must not clear a newer unacked OCR frame (seq mismatch guard).
    var spool = OutboundSpool()
    spool.setUnacked(Data("frame-7".utf8), seq: 7)
    #expect(spool.onAck(6) == false)
    spool.setUnacked(Data("frame-8".utf8), seq: 8)
    #expect(spool.onAck(7) == false)
    #expect(spool.onAck(8) == true)
    let drained = spool.drainOnConnect()
    #expect(drained.isEmpty)
}
