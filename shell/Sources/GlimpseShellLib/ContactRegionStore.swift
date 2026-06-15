import Foundation

/// Persists the calibrated contact-name region (CG rect). Mirror of RegionStore.
public enum ContactRegionStore {
    private struct Stored: Codable {
        var x: Double
        var y: Double
        var w: Double
        var h: Double
    }

    public static var url: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/contact-region.json")
    }

    public static func save(_ rect: CGRect, to url: URL = ContactRegionStore.url) {
        try? FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let stored = Stored(x: rect.minX, y: rect.minY, w: rect.width, h: rect.height)
        try? JSONEncoder().encode(stored).write(to: url)
    }

    public static func load(from url: URL = ContactRegionStore.url) -> CGRect? {
        guard let data = try? Data(contentsOf: url),
            let s = try? JSONDecoder().decode(Stored.self, from: data)
        else { return nil }
        return CGRect(x: s.x, y: s.y, width: s.w, height: s.h)
    }
}
