import CoreGraphics
import Foundation
import ImageIO
import Testing
@testable import GlimpseShellLib

private func solidImage(width: Int, height: Int) -> CGImage {
    let cs = CGColorSpaceCreateDeviceRGB()
    let ctx = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
        space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    )!
    ctx.setFillColor(CGColor(red: 0.2, green: 0.4, blue: 0.6, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
    return ctx.makeImage()!
}

@Test
func downscaledJPEGBase64ProducesDecodableImageUnderCap() throws {
    let big = solidImage(width: 3000, height: 2000)
    let b64 = ImageUtil.downscaledJPEGBase64(big, maxDimension: 1024, quality: 0.6)
    let b64v = try #require(b64)
    #expect(!b64v.isEmpty)
    let data = try #require(Data(base64Encoded: b64v))
    let src = try #require(CGImageSourceCreateWithData(data as CFData, nil))
    let decoded = try #require(CGImageSourceCreateImageAtIndex(src, 0, nil))
    #expect(max(decoded.width, decoded.height) <= 1024)
}
