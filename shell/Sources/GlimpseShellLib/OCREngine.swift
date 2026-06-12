import CoreGraphics
import Vision

public enum OCR {
    /// Recognize text in a region image. Returns blocks ordered top-to-bottom,
    /// with x-extents normalized [0,1] within the image (Vision's own space).
    ///
    /// Heavy (100-300 ms in accurate mode) — must be called off the main thread.
    /// Bounding boxes assume horizontal text; vertical CJK layouts would swap
    /// the meaning of the x-extents (out of scope for v1 chat windows).
    public static func recognize(_ image: CGImage) throws -> [Block] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["zh-Hans", "en-US"]
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: image)
        try handler.perform([request])
        let observations = request.results ?? []
        return observations
            // Vision bounding boxes have origin at BOTTOM-left; higher midY = higher on screen.
            .sorted { $0.boundingBox.midY > $1.boundingBox.midY }
            .compactMap { observation -> Block? in
                guard let candidate = observation.topCandidates(1).first else { return nil }
                // Vision can emit values a hair outside [0,1] for edge-touching
                // text; the brain rejects out-of-range blocks (frame silently
                // dropped), so clamp at the source.
                return Block(
                    text: candidate.string,
                    x0: max(0.0, min(1.0, observation.boundingBox.minX)),
                    x1: max(0.0, min(1.0, observation.boundingBox.maxX)),
                    conf: Double(candidate.confidence)
                )
            }
    }
}
