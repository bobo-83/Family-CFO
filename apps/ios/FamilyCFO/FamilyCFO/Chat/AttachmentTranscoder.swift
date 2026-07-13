import Foundation
import UIKit

/// Normalizes picked attachments for `POST /chat/messages`: images become
/// bounded JPEGs (HEIC and oversized photos transcoded down), PDFs (M84) and
/// data files (M85) pass through with a size cap. The server's upload cap is
/// 10 MB (M18); base64 inflates by 4/3, so raw payloads stay under 7 MB.
enum AttachmentTranscoder {
    static let maxRawBytes = 7_000_000
    static let maxImageDimension: CGFloat = 2048

    enum TranscodeError: Error, LocalizedError, Equatable {
        case undecodableImage
        case cannotEncodeUnderCap
        case pdfTooLarge(Int)
        case dataFileTooLarge(Int)

        var errorDescription: String? {
            switch self {
            case .undecodableImage:
                return "That image couldn't be read."
            case .cannotEncodeUnderCap:
                return "That image is too large to send, even after compression."
            case .pdfTooLarge(let bytes):
                return "That PDF is \(bytes / 1_000_000) MB — the server accepts up to \(maxRawBytes / 1_000_000) MB."
            case .dataFileTooLarge(let bytes):
                return "That file is \(bytes / 1_000_000) MB — the server accepts up to \(maxRawBytes / 1_000_000) MB."
            }
        }
    }

    /// JPEG/PNG/WebP under the cap pass through untouched (the server
    /// understands all three); everything else — HEIC included — is
    /// re-encoded as a bounded JPEG.
    static func image(from data: Data, displayName: String) throws -> ChatAttachment {
        if data.count <= maxRawBytes, let passthrough = passthroughMediaType(for: data) {
            return ChatAttachment(data: data, kind: .visual(passthrough), displayName: displayName)
        }
        guard let image = UIImage(data: data) else {
            throw TranscodeError.undecodableImage
        }
        let scaled = downscale(image, maxDimension: maxImageDimension)
        for quality in [0.8, 0.6, 0.4, 0.2] {
            if let jpeg = scaled.jpegData(compressionQuality: quality), jpeg.count <= maxRawBytes {
                return ChatAttachment(
                    data: jpeg, kind: .visual(.imageJpeg), displayName: displayName)
            }
        }
        throw TranscodeError.cannotEncodeUnderCap
    }

    static func pdf(from data: Data, displayName: String) throws -> ChatAttachment {
        guard data.count <= maxRawBytes else {
            throw TranscodeError.pdfTooLarge(data.count)
        }
        return ChatAttachment(
            data: data, kind: .visual(.applicationPdf), displayName: displayName)
    }

    /// Data files (M85) go up verbatim: the server parses CSV / XLSX / text
    /// into a bounded grounded preview, so there is nothing to transcode
    /// client-side — only the shared size cap to enforce. The filename rides
    /// along because the server sniffs the format from its extension.
    static func dataFile(from data: Data, displayName: String) throws -> ChatAttachment {
        guard data.count <= maxRawBytes else {
            throw TranscodeError.dataFileTooLarge(data.count)
        }
        return ChatAttachment(data: data, kind: .dataFile, displayName: displayName)
    }

    /// Sniffs the contract media types by magic bytes; HEIC returns nil so
    /// it gets transcoded.
    static func passthroughMediaType(
        for data: Data
    ) -> Components.Schemas.ChatRequest.ImageMediaTypePayload? {
        if data.starts(with: [0xFF, 0xD8, 0xFF]) { return .imageJpeg }
        if data.starts(with: [0x89, 0x50, 0x4E, 0x47]) { return .imagePng }
        if data.count > 12, data.starts(with: Array("RIFF".utf8)),
            data[8..<12].elementsEqual(Array("WEBP".utf8))
        {
            return .imageWebp
        }
        return nil
    }

    private static func downscale(_ image: UIImage, maxDimension: CGFloat) -> UIImage {
        let largest = max(image.size.width, image.size.height)
        guard largest > maxDimension else { return image }
        let scale = maxDimension / largest
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
    }
}
