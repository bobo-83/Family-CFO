import Foundation
import UIKit
import Testing

@testable import FamilyCFO

/// M89 receipt capture. The rule that matters: when the phone can read the
/// receipt itself, the PHOTO NEVER LEAVES THE DEVICE — only the text does
/// (ADR 0011 backlog).
struct ReceiptCaptureTests {
    private let photo = ChatAttachment(
        data: Data("jpeg".utf8), kind: .visual(.imageJpeg), displayName: "Receipt")

    @Test func readableReceiptSendsTextOnlyAndKeepsThePhotoOnThePhone() {
        let message = ReceiptCapture.message(
            recognizedLines: ["WHOLE FOODS", "Total $42.99", "2026-07-13"],
            fallbackImage: photo
        )

        #expect(message.attachment == nil)
        #expect(message.text.contains("WHOLE FOODS"))
        #expect(message.text.contains("Total $42.99"))
    }

    /// A blurry shot yields a stray fragment or two. Sending that as "what the
    /// receipt says" would be worse than sending nothing, so the photo goes to
    /// the server's vision model instead — the unchanged M84 path.
    @Test func unreadableReceiptFallsBackToTheServerVisionModel() {
        let message = ReceiptCapture.message(recognizedLines: ["W"], fallbackImage: photo)

        #expect(message.attachment == photo)
        #expect(message.text.contains("couldn't read"))
    }

    @Test func blankLinesDoNotCountAsRecognizedText() {
        let message = ReceiptCapture.message(
            recognizedLines: ["  ", "", "\n"], fallbackImage: photo)

        #expect(message.attachment == photo)
    }
}

@MainActor
final class MockIncomeAPI: IncomeAPI, @unchecked Sendable {
    var scanResult: Components.Schemas.W2ScanResult?
    var scanError: Error?
    var createError: Error?
    private(set) var created: [Components.Schemas.IncomeEarnerCreateRequest] = []

    nonisolated func scanW2(_ attachment: ChatAttachment) async throws
        -> Components.Schemas.W2ScanResult
    {
        try await MainActor.run {
            if let scanError { throw scanError }
            return scanResult!
        }
    }

    nonisolated func createEarner(
        _ request: Components.Schemas.IncomeEarnerCreateRequest
    ) async throws {
        try await MainActor.run {
            if let createError { throw createError }
            created.append(request)
        }
    }
}

@MainActor
struct W2ScanViewModelTests {
    private func scanResult(
        year: Int? = 2025,
        employer: String? = "ACME CORP",
        wagesMinor: Int? = 12_345_600,
        withheldMinor: Int? = 2_100_050
    ) -> Components.Schemas.W2ScanResult {
        .init(
            year: year,
            employer: employer,
            wagesMinor: wagesMinor,
            federalWithheldMinor: withheldMinor,
            note: "Read Box 1 and Box 2 from page 1."
        )
    }

    /// M73's rule, which the whole flow exists to honour: a vision model never
    /// writes financial ground truth. The scan only fills the form in.
    @Test func scanningPrefillsTheFormAndSavesNothing() async {
        let api = MockIncomeAPI()
        api.scanResult = scanResult()
        let viewModel = W2ScanViewModel(api: api)

        viewModel.apply(scanResult())

        #expect(viewModel.form.year == 2025)
        #expect(viewModel.form.label == "ACME CORP")
        #expect(viewModel.form.wages == Decimal(string: "123456"))
        #expect(viewModel.form.withheld == Decimal(string: "21000.5"))
        #expect(viewModel.scanNote != nil)
        #expect(api.created.isEmpty)
        #expect(!viewModel.didSave)
    }

    /// The user's own correction outranks the model's reading.
    @Test func scanNeverOverwritesALabelTheUserAlreadyTyped() {
        let viewModel = W2ScanViewModel(api: MockIncomeAPI())
        viewModel.form.label = "Alex — day job"

        viewModel.apply(scanResult(employer: "ACME CORP"))

        #expect(viewModel.form.label == "Alex — day job")
    }

    @Test func scanOnlyFillsWhatItActuallyFound() {
        let viewModel = W2ScanViewModel(api: MockIncomeAPI())
        viewModel.form.year = 2024

        viewModel.apply(scanResult(year: nil, employer: nil, wagesMinor: nil, withheldMinor: nil))

        #expect(viewModel.form.year == 2024)
        #expect(viewModel.form.wages == nil)
    }

    @Test func addingTheEarnerSendsMinorUnits() async {
        let api = MockIncomeAPI()
        let viewModel = W2ScanViewModel(api: api)
        viewModel.apply(scanResult())

        await viewModel.addEarner()

        #expect(api.created.count == 1)
        #expect(api.created[0].label == "ACME CORP")
        #expect(api.created[0].w2Year == 2025)
        #expect(api.created[0].w2WagesMinor == 12_345_600)
        #expect(api.created[0].w2WithheldMinor == 2_100_050)
        #expect(viewModel.didSave)
    }

    /// Money crosses the contract in minor units, rounded through Decimal —
    /// binary floating point mangles cents often enough to matter on a tax
    /// figure.
    @Test func dollarsRoundToMinorUnitsExactly() {
        #expect(W2ScanViewModel.minorUnits(Decimal(string: "4412.35")!) == 441_235)
        #expect(W2ScanViewModel.minorUnits(Decimal(string: "0.005")!) == 1)
        #expect(W2ScanViewModel.minorUnits(Decimal(string: "123456")!) == 12_345_600)
    }

    @Test func anEarnerWithoutALabelCannotBeSaved() async {
        let api = MockIncomeAPI()
        let viewModel = W2ScanViewModel(api: api)
        viewModel.form.label = "   "

        #expect(!viewModel.canSave)
        await viewModel.addEarner()
        #expect(api.created.isEmpty)
    }

    /// A real (encodable) photo — an empty `UIImage` would fail to JPEG-encode
    /// and never reach the API, testing nothing.
    private func photo() -> UIImage {
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: 40, height: 40), format: format)
            .image { context in
                UIColor.white.setFill()
                context.fill(CGRect(x: 0, y: 0, width: 40, height: 40))
            }
    }

    @Test func aForbiddenScanSaysWhoMayDoIt() async {
        let api = MockIncomeAPI()
        api.scanError = IncomeAPIError.forbidden
        let viewModel = W2ScanViewModel(api: api)

        await viewModel.scan(photo())

        #expect(viewModel.errorMessage?.contains("owner or adult") == true)
        #expect(!viewModel.didSave)
    }

    @Test func anUnreadableScanLeavesTheFormTypeableByHand() async {
        let api = MockIncomeAPI()
        api.scanError = IncomeAPIError.unreadableScan
        let viewModel = W2ScanViewModel(api: api)

        await viewModel.scan(photo())

        #expect(viewModel.errorMessage?.contains("Couldn't read") == true)
        // The form is untouched, so the user can still type the figures in.
        #expect(viewModel.form == W2ScanViewModel.Form())
    }
}
