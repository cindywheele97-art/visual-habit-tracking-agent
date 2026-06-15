import AppKit

/// Periodically captures + OCRs the calibrated contact-name region and exposes the
/// latest detected name. Decoupled from the per-frame OCR path (names change
/// rarely), so the capture path just reads `current`.
public final class ContactReader {
    private let region: () -> CGRect?
    private var timer: Timer?
    // Written on main (the timer/MainActor hop), read from the capture queue in
    // processFrame. A deliberate, benign display-only race on a value-type String
    // (a name shown for the operator's awareness, never a correctness input) —
    // not worth actor isolation under the package's main-confined-by-convention
    // model. See WatchFlags for the same confinement-by-comment pattern.
    public private(set) var current: String = ""

    public init(region: @escaping () -> CGRect? = { ContactRegionStore.load() }) {
        self.region = region
    }

    public func start(interval: TimeInterval = 3) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        refresh()
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func refresh() {
        guard let rect = region() else { return }
        Task {
            guard let image = try? await ClickSnapshot.captureRect(rect) else { return }
            let name = ((try? OCR.recognize(image)) ?? [])
                .map(\.text).joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            await MainActor.run { self.current = name }
        }
    }
}
