import CoreGraphics
import Foundation
import Testing
@testable import GlimpseShellLib

@Test
func regionSelectorTinyDragIsCancel() {
    // WHY: a one-pixel slip must not overwrite a saved calibration — treat as cancel.
    #expect(RegionSelector.isValidSelection(CGRect(x: 0, y: 0, width: 10, height: 10)))
    #expect(!RegionSelector.isValidSelection(CGRect(x: 0, y: 0, width: 9, height: 10)))
    #expect(!RegionSelector.isValidSelection(CGRect(x: 0, y: 0, width: 10, height: 9)))
}
