import CoreGraphics
import CoreImage
import CoreVideo
import ImageIO

public enum ImageUtil {
    private static let context = CIContext()

    public static func cgImage(from pixelBuffer: CVPixelBuffer) -> CGImage? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        return context.createCGImage(ciImage, from: ciImage.extent)
    }

    /// Downscale so the longest side is <= maxDimension, JPEG-encode, base64.
    /// Keeps the conversation-region screenshot small enough for the socket + Claude.
    /// Returns nil if the result exceeds maxBase64Length: OcrMsg travels as one
    /// NDJSON line and must stay under the brain's IPC line limit — an
    /// over-budget frame ships no image (fail-soft to text) rather than a line
    /// the brain would reject.
    public static func downscaledJPEGBase64(
        _ image: CGImage, maxDimension: CGFloat = 1024, quality: CGFloat = 0.6,
        maxBase64Length: Int = 512 * 1024
    ) -> String? {
        let w = CGFloat(image.width), h = CGFloat(image.height)
        let scale = min(1, maxDimension / max(w, h))
        let outW = Int((w * scale).rounded()), outH = Int((h * scale).rounded())
        guard outW > 0, outH > 0,
            let ctx = CGContext(
                data: nil, width: outW, height: outH, bitsPerComponent: 8, bytesPerRow: 0,
                space: CGColorSpaceCreateDeviceRGB(),
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else { return nil }
        ctx.interpolationQuality = .medium
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: outW, height: outH))
        guard let scaled = ctx.makeImage() else { return nil }

        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(
            data as CFMutableData, "public.jpeg" as CFString, 1, nil
        ) else { return nil }
        CGImageDestinationAddImage(dest, scaled, [
            kCGImageDestinationLossyCompressionQuality: quality
        ] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        let b64 = (data as Data).base64EncodedString()
        return b64.count <= maxBase64Length ? b64 : nil
    }
}
