import CoreImage
import CoreVideo

public enum ImageUtil {
    private static let context = CIContext()

    public static func cgImage(from pixelBuffer: CVPixelBuffer) -> CGImage? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        return context.createCGImage(ciImage, from: ciImage.extent)
    }
}
