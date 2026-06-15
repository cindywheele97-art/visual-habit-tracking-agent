import CoreGraphics
import ScreenCaptureKit

/// Geometry + one-shot capture for click snapshots. (Capture func added in Task 7.)
public enum ClickSnapshot {
    /// A `size`-sized rect centered on `point`, clamped to stay within `display`.
    /// If `display` is smaller than `size` on an axis, that axis fills the display.
    public static func rect(around point: CGPoint, in display: CGRect, size: CGSize) -> CGRect {
        let w = min(size.width, display.width)
        let h = min(size.height, display.height)
        var x = point.x - w / 2
        var y = point.y - h / 2
        x = max(display.minX, min(x, display.maxX - w))
        y = max(display.minY, min(y, display.maxY - h))
        return CGRect(x: x, y: y, width: w, height: h)
    }

    /// One-shot screenshot of a `size`-sized region centered on a global-coordinate
    /// `point` (CG origin: top-left). Returns nil if no display contains the point.
    public static func captureAround(point: CGPoint, size: CGSize) async throws -> CGImage? {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true
        )
        guard
            let scDisplay = content.displays.first(where: {
                CGDisplayBounds($0.displayID).contains(point)
            })
        else { return nil }

        let bounds = CGDisplayBounds(scDisplay.displayID)
        let globalRect = rect(around: point, in: bounds, size: size)
        return try await captureGlobalRect(globalRect, on: scDisplay, displayBounds: bounds)
    }

    /// One-shot screenshot of an arbitrary global-coordinate rect.
    /// Returns nil if no display contains the rect's origin.
    public static func captureRect(_ globalRect: CGRect) async throws -> CGImage? {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true
        )
        guard
            let scDisplay = content.displays.first(where: {
                CGDisplayBounds($0.displayID).contains(globalRect.origin)
            })
        else { return nil }

        let bounds = CGDisplayBounds(scDisplay.displayID)
        return try await captureGlobalRect(globalRect, on: scDisplay, displayBounds: bounds)
    }

    // MARK: - Private

    /// Shared screenshot core: converts a global rect to display-local coords and
    /// captures it via SCScreenshotManager. `scDisplay` must be the display that
    /// owns the rect.
    private static func captureGlobalRect(
        _ globalRect: CGRect, on scDisplay: SCDisplay, displayBounds bounds: CGRect
    ) async throws -> CGImage? {
        let local = CGRect(
            x: globalRect.minX - bounds.minX, y: globalRect.minY - bounds.minY,
            width: globalRect.width, height: globalRect.height
        )
        let config = SCStreamConfiguration()
        config.sourceRect = local
        config.width = Int(local.width) * 2   // retina-density pixels for OCR quality
        config.height = Int(local.height) * 2
        let filter = SCContentFilter(display: scDisplay, excludingWindows: [])
        return try await SCScreenshotManager.captureImage(
            contentFilter: filter, configuration: config
        )
    }
}
