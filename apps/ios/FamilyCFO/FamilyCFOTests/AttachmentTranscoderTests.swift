import Testing
import UIKit

@testable import FamilyCFO

struct AttachmentTranscoderTests {
    private func jpegData(side: CGFloat) -> Data {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let image = UIGraphicsImageRenderer(
            size: CGSize(width: side, height: side), format: format
        ).image { context in
            UIColor.systemTeal.setFill()
            context.fill(CGRect(x: 0, y: 0, width: side, height: side))
        }
        return image.jpegData(compressionQuality: 0.9)!
    }

    @Test func smallJPEGPassesThroughUntouched() throws {
        let data = jpegData(side: 100)

        let attachment = try AttachmentTranscoder.image(from: data, displayName: "Photo")

        #expect(attachment.kind == .visual(.imageJpeg))
        #expect(attachment.data == data)
    }

    @Test func sniffsContractMediaTypes() {
        #expect(
            AttachmentTranscoder.passthroughMediaType(for: Data([0xFF, 0xD8, 0xFF, 0xE0]))
                == .imageJpeg)
        #expect(
            AttachmentTranscoder.passthroughMediaType(
                for: Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A])) == .imagePng)
        var webp = Data("RIFF".utf8)
        webp.append(Data([0x00, 0x00, 0x00, 0x00]))
        webp.append(Data("WEBPVP8 ".utf8))
        #expect(AttachmentTranscoder.passthroughMediaType(for: webp) == .imageWebp)
        // HEIC (ftyp box) is not a contract type — must be transcoded.
        var heic = Data([0x00, 0x00, 0x00, 0x18])
        heic.append(Data("ftypheic".utf8))
        #expect(AttachmentTranscoder.passthroughMediaType(for: heic) == nil)
    }

    @Test func pdfUnderTheCapPassesThrough() throws {
        let data = Data("%PDF-1.4 tiny".utf8)

        let attachment = try AttachmentTranscoder.pdf(from: data, displayName: "w2.pdf")

        #expect(attachment.kind == .visual(.applicationPdf))
        #expect(attachment.displayName == "w2.pdf")
    }

    @Test func pdfOverTheCapIsRejected() {
        let oversized = Data(count: AttachmentTranscoder.maxRawBytes + 1)

        #expect(throws: AttachmentTranscoder.TranscodeError.pdfTooLarge(oversized.count)) {
            try AttachmentTranscoder.pdf(from: oversized, displayName: "big.pdf")
        }
    }

    /// M85: data files go up verbatim, and the filename goes with them — the
    /// server sniffs CSV vs XLSX vs text from the extension, so losing the name
    /// would lose the format.
    @Test func dataFilePassesThroughCarryingItsFilename() throws {
        let data = Data("month,spend\nJan,412\n".utf8)

        let attachment = try AttachmentTranscoder.dataFile(from: data, displayName: "spend.csv")

        #expect(attachment.kind == .dataFile)
        #expect(attachment.data == data)
        #expect(attachment.displayName == "spend.csv")
    }

    @Test func dataFileOverTheCapIsRejected() {
        let oversized = Data(count: AttachmentTranscoder.maxRawBytes + 1)

        #expect(throws: AttachmentTranscoder.TranscodeError.dataFileTooLarge(oversized.count)) {
            try AttachmentTranscoder.dataFile(from: oversized, displayName: "huge.xlsx")
        }
    }

    @Test func undecodableImageIsRejected() {
        #expect(throws: AttachmentTranscoder.TranscodeError.undecodableImage) {
            try AttachmentTranscoder.image(
                from: Data(count: AttachmentTranscoder.maxRawBytes + 1), displayName: "junk")
        }
    }
}
