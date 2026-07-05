import AppKit
import os

/// Periodically captures + OCRs the calibrated contact-name region and exposes the
/// latest detected name. Decoupled from the per-frame OCR path (names change
/// rarely), so the capture path just reads `current`.
public final class ContactReader {
    private let region: () -> CGRect?
    private var timer: Timer?
    private let currentLock = OSAllocatedUnfairLock(initialState: "")

    public var current: String {
        currentLock.withLock { $0 }
    }

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
        guard region() != nil else { return }  // keep `current` untouched when uncalibrated
        readFresh { _ in }
    }

    /// One immediate capture+OCR of the contact region, bypassing the poll
    /// cache: auto-send's wrong-chat gate needs a value fresher than the timer
    /// interval. Completion runs on the main queue. An uncalibrated or
    /// unreadable region yields "" — callers treating a known contact as armed
    /// fail closed on that.
    public func readFresh(completion: @escaping (String) -> Void) {
        guard let rect = region() else {
            completion("")
            return
        }
        Task {
            guard let image = try? await ClickSnapshot.captureRect(rect) else {
                await MainActor.run { completion("") }
                return
            }
            let name = ((try? OCR.recognize(image)) ?? [])
                .map(\.text).joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            await MainActor.run {
                self.currentLock.withLock { $0 = name }
                completion(name)
            }
        }
    }
}
