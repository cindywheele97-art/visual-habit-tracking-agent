import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func contactRegionStoreRoundTripsRect() throws {
    let tmp = FileManager.default.temporaryDirectory
        .appendingPathComponent("cr-\(UUID()).json")
    defer { try? FileManager.default.removeItem(at: tmp) }
    let rect = CGRect(x: 10, y: 20, width: 300, height: 40)
    ContactRegionStore.save(rect, to: tmp)
    #expect(ContactRegionStore.load(from: tmp) == rect)
}

@Test
func contactRegionStoreFailsClosedOnMissingFile() {
    let missing = FileManager.default.temporaryDirectory
        .appendingPathComponent("nope-\(UUID()).json")
    #expect(ContactRegionStore.load(from: missing) == nil)
}
