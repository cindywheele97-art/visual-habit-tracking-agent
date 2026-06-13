import Foundation

/// Reads an opt-in list of app bundle IDs from JSON. Fail-closed: any read or
/// parse problem yields an empty list, so nothing is captured.
public final class AppAllowlist {
    private let allowed: Set<String>

    public init(path: URL) {
        guard
            let data = try? Data(contentsOf: path),
            let ids = try? JSONDecoder().decode([String].self, from: data)
        else {
            allowed = []
            return
        }
        allowed = Set(ids)
    }

    /// Default location: ~/.glimpse/allowlist.json
    public static var defaultPath: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".glimpse/allowlist.json")
    }

    public func isAllowed(_ bundleId: String?) -> Bool {
        guard let bundleId else { return false }
        return allowed.contains(bundleId)
    }
}
