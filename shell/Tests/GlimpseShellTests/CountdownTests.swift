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
