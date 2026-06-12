import CoreVideo
import Foundation

public enum Diff {
    /// Mean absolute difference between equal-length sample arrays, normalized 0...1.
    public static func score(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard !a.isEmpty, a.count == b.count else { return 1.0 }
        var total = 0
        for i in a.indices {
            total += abs(Int(a[i]) - Int(b[i]))
        }
        return Double(total) / Double(a.count) / 255.0
    }

    /// Samples the green channel of a BGRA pixel buffer on a grid×grid lattice.
    public static func sample(_ pixelBuffer: CVPixelBuffer, grid: Int = 32) -> [UInt8] {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else { return [] }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let rowBytes = CVPixelBufferGetBytesPerRow(pixelBuffer)
        guard width > 0, height > 0 else { return [] }
        let ptr = base.assumingMemoryBound(to: UInt8.self)
        var samples: [UInt8] = []
        samples.reserveCapacity(grid * grid)
        for gy in 0..<grid {
            for gx in 0..<grid {
                let x = min(width - 1, gx * width / grid)
                let y = min(height - 1, gy * height / grid)
                samples.append(ptr[y * rowBytes + x * 4 + 1])  // G of BGRA
            }
        }
        return samples
    }
}

public final class DiffGate {
    private var previous: [UInt8]?
    private let threshold: Double

    public init(threshold: Double = 0.02) {
        self.threshold = threshold
    }

    /// True when the frame differs enough from the previous one to be worth OCR.
    public func isChanged(_ samples: [UInt8]) -> Bool {
        defer { previous = samples }
        guard let prev = previous else { return true }
        return Diff.score(prev, samples) > threshold
    }
}
