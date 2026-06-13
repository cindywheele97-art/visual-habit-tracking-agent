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
