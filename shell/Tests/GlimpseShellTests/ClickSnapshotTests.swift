import CoreGraphics
import Testing
@testable import GlimpseShellLib

@Test
func snapshotRectIsCenteredAndClampedToDisplay() {
    let display = CGRect(x: 0, y: 0, width: 1000, height: 800)

    let mid = ClickSnapshot.rect(around: CGPoint(x: 500, y: 400), in: display, size: CGSize(width: 600, height: 400))
    #expect(mid == CGRect(x: 200, y: 200, width: 600, height: 400))

    let corner = ClickSnapshot.rect(around: CGPoint(x: 10, y: 10), in: display, size: CGSize(width: 600, height: 400))
    #expect(corner.minX == 0 && corner.minY == 0)
    #expect(corner.width == 600 && corner.height == 400)

    let br = ClickSnapshot.rect(around: CGPoint(x: 990, y: 790), in: display, size: CGSize(width: 600, height: 400))
    #expect(br.maxX <= 1000 && br.maxY <= 800)
    #expect(br.width == 600 && br.height == 400)
}

@Test
func snapshotRectShrinksToFitTinyDisplay() {
    let small = CGRect(x: 0, y: 0, width: 300, height: 200)
    let r = ClickSnapshot.rect(around: CGPoint(x: 150, y: 100), in: small, size: CGSize(width: 600, height: 400))
    #expect(r == small)
}
