import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func identicalSamplesScoreZero() {
    let a: [UInt8] = [10, 20, 30, 40]
    #expect(Diff.score(a, a) == 0.0)
}

@Test
func oppositeSamplesScoreOne() {
    let a = [UInt8](repeating: 0, count: 16)
    let b = [UInt8](repeating: 255, count: 16)
    #expect(abs(Diff.score(a, b) - 1.0) < 0.001)
}

@Test
func mismatchedLengthsCountAsChanged() {
    #expect(Diff.score([1, 2], [1, 2, 3]) == 1.0)
}

// WHY: the gate is the battery story — unchanged frames must never reach
// OCR (spec §3), but the very first frame always must.
@Test
func gateFirstFrameAlwaysChanged() {
    let gate = DiffGate(threshold: 0.02)
    #expect(gate.isChanged([50, 50, 50]))
    #expect(!gate.isChanged([50, 50, 50]))
    #expect(gate.isChanged([50, 50, 250]))
}

@Test
func diffGateHoldsBaselineOnUnchangedFrames() {
    // WHY: updating baseline every sub-threshold frame resets the reference — gradual
    // UI fades never accumulate enough delta to trigger OCR.
    let gate = DiffGate(threshold: 0.02)
    let n = 64
    let base = [UInt8](repeating: 50, count: n)
    #expect(gate.isChanged(base))
    var triggered = false
    for step in 1...10 {
        let drifted = [UInt8](repeating: 50 + UInt8(step), count: n)
        if gate.isChanged(drifted) {
            triggered = true
            break
        }
    }
    #expect(triggered)
}
