import Foundation

/// Persists the calibrated chat input-box point (CG coordinates) across restarts.
/// Mirror of RegionStore. Default location: ~/.glimpse/input-box.json
public enum InputBoxStore {
    private struct Stored: Codable {
        var x: Double
        var y: Double
    }

    public static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/input-box.json")
    }

    public static func save(_ point: CGPoint, to url: URL = InputBoxStore.url) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let stored = Stored(x: point.x, y: point.y)
        try? JSONEncoder().encode(stored).write(to: url)
    }

    public static func load(from url: URL = InputBoxStore.url) -> CGPoint? {
        guard let data = try? Data(contentsOf: url),
            let stored = try? JSONDecoder().decode(Stored.self, from: data)
        else { return nil }
        return CGPoint(x: stored.x, y: stored.y)
    }
}
