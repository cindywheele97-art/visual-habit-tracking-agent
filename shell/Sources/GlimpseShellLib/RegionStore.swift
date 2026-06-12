import Foundation

/// Persists the selected region (CG coordinates) across restarts.
public enum RegionStore {
    private struct Stored: Codable {
        var x: Double
        var y: Double
        var w: Double
        var h: Double
    }

    public static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/region.json")
    }

    public static func save(_ rect: CGRect) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let stored = Stored(x: rect.minX, y: rect.minY, w: rect.width, h: rect.height)
        try? JSONEncoder().encode(stored).write(to: url)
    }

    public static func load() -> CGRect? {
        guard let data = try? Data(contentsOf: url),
            let stored = try? JSONDecoder().decode(Stored.self, from: data)
        else { return nil }
        return CGRect(x: stored.x, y: stored.y, width: stored.w, height: stored.h)
    }
}
