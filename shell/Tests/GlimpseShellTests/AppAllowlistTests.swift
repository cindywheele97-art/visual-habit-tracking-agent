import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func allowlistMatchesExactBundleIds() throws {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let file = dir.appendingPathComponent("allowlist.json")
    try #"["com.google.Chrome","com.apple.Safari"]"#.write(to: file, atomically: true, encoding: .utf8)

    let list = AppAllowlist(path: file)
    #expect(list.isAllowed("com.google.Chrome"))
    #expect(list.isAllowed("com.apple.Safari"))
    #expect(!list.isAllowed("com.apple.Notes"))
    #expect(!list.isAllowed(nil))  // unknown foreground app
}

@Test
func allowlistFailsClosedOnMissingOrMalformedFile() {
    let missing = FileManager.default.temporaryDirectory.appendingPathComponent("nope-\(UUID()).json")
    #expect(!AppAllowlist(path: missing).isAllowed("com.google.Chrome"))

    let badDir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try? FileManager.default.createDirectory(at: badDir, withIntermediateDirectories: true)
    let bad = badDir.appendingPathComponent("allowlist.json")
    try? "{ not json".write(to: bad, atomically: true, encoding: .utf8)
    #expect(!AppAllowlist(path: bad).isAllowed("com.google.Chrome"))
}
