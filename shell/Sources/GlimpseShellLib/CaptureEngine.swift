import CoreMedia
import ScreenCaptureKit

public enum CaptureError: Error {
    case noDisplayForRegion
}

/// NSObject bridge for SCStream callbacks — actor isolation cannot inherit NSObjectProtocol.
private final class CaptureStreamBridge: NSObject, SCStreamOutput, SCStreamDelegate {
    // Written in CaptureEngine.start before capture begins; read on frameQueue from callbacks.
    nonisolated(unsafe) var onFrame: ((CVPixelBuffer) -> Void)?

    nonisolated func stream(
        _ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .screen, sampleBuffer.isValid,
            let attachments = CMSampleBufferGetSampleAttachmentsArray(
                sampleBuffer, createIfNecessary: false
            ) as? [[SCStreamFrameInfo: Any]],
            let statusRaw = attachments.first?[.status] as? Int,
            statusRaw == SCFrameStatus.complete.rawValue,
            let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer)
        else { return }
        onFrame?(pixelBuffer)
    }

    nonisolated func stream(_ stream: SCStream, didStopWithError error: Error) {
        NSLog("capture stopped: \(error.localizedDescription)")
    }
}

/// Push-based region capture. SCK only delivers frames when content changes;
/// `minimumFrameInterval` caps delivery at 2 fps (spec §3).
public actor CaptureEngine {
    private var stream: SCStream?
    private let bridge = CaptureStreamBridge()
    private let frameQueue = DispatchQueue(label: "glimpse.capture")

    public init() {}

    /// `region` is in CG global coordinates (origin top-left of primary display).
    public func start(region: CGRect, onFrame: @escaping (CVPixelBuffer) -> Void) async throws {
        await stop()  // no-op on first call; on re-select it stops the old stream
        bridge.onFrame = onFrame
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: true
        )
        guard
            let display = content.displays.first(where: {
                CGDisplayBounds($0.displayID).contains(CGPoint(x: region.midX, y: region.midY))
            })
        else { throw CaptureError.noDisplayForRegion }

        let bounds = CGDisplayBounds(display.displayID)
        let local = CGRect(
            x: region.minX - bounds.minX, y: region.minY - bounds.minY,
            width: region.width, height: region.height
        )
        let config = SCStreamConfiguration()
        config.sourceRect = local
        config.width = Int(local.width) * 2   // retina-density pixels for OCR quality
        config.height = Int(local.height) * 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 2)
        config.pixelFormat = kCVPixelFormatType_32BGRA
        config.queueDepth = 3
        config.showsCursor = false

        let filter = SCContentFilter(display: display, excludingWindows: [])
        let stream = SCStream(filter: filter, configuration: config, delegate: bridge)
        try stream.addStreamOutput(bridge, type: .screen, sampleHandlerQueue: frameQueue)
        try await stream.startCapture()
        self.stream = stream
    }

    public func stop() async {
        try? await stream?.stopCapture()
        stream = nil
        bridge.onFrame = nil
    }
}
