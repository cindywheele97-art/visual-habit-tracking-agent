import CoreGraphics
import Vision

public enum OCR {
    /// Recognize text in a region image. Returns blocks ordered top-to-bottom,
    /// with x-extents normalized [0,1] within the image (Vision's own space).
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
                return Block(
                    text: candidate.string,
                    x0: observation.boundingBox.minX,
                    x1: observation.boundingBox.maxX,
                    conf: Double(candidate.confidence)
                )
            }
    }
}
