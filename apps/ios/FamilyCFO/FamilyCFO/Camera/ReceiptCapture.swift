import Foundation
import UIKit
import Vision

/// Reads a receipt's text on the phone (M89). ADR 0011's backlog note: the
/// native app should prefer describing a photo on-device and sending only the
/// text, so the photo never leaves the phone and the box needs no vision model.
enum ReceiptTextRecognizer {
    static func recognizedLines(in image: UIImage) async -> [String] {
        guard let cgImage = image.cgImage else { return [] }
        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        do {
            let observations = try await request.perform(on: cgImage)
            return observations.compactMap { $0.topCandidates(1).first?.string }
        } catch {
            // OCR failing is not an error the user should see — it just means
            // the photo goes to the server's vision model instead.
            return []
        }
    }
}

/// Decides what actually leaves the phone after a receipt capture.
enum ReceiptCapture {
    struct Message: Equatable {
        let text: String
        /// nil when the phone read the receipt itself — then only text is sent.
        let attachment: ChatAttachment?
    }

    /// A blurry or badly-lit shot still yields a stray fragment or two. Below
    /// this many lines the OCR has effectively failed, and sending the model a
    /// scrap while calling it "what the receipt says" would be worse than
    /// sending nothing — so the photo goes to the server's vision model
    /// instead (the unchanged M84 path).
    static let minimumUsableLines = 3

    static func message(recognizedLines: [String], fallbackImage: ChatAttachment?) -> Message {
        let lines = recognizedLines
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }

        guard lines.count >= minimumUsableLines else {
            return Message(
                text: """
                    I photographed a receipt, but this phone couldn't read its text. \
                    Describe it — merchant, date, total — and tell me how this purchase \
                    sits against our budget.
                    """,
                attachment: fallbackImage
            )
        }

        return Message(
            text: """
                I photographed a receipt. This phone read the following text from it:

                \(lines.joined(separator: "\n"))

                Summarise it — merchant, date, total — and tell me how this purchase sits \
                against our budget.
                """,
            attachment: nil
        )
    }
}
