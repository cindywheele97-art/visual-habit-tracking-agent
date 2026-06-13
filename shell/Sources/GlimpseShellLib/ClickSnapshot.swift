import CoreGraphics

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
}
