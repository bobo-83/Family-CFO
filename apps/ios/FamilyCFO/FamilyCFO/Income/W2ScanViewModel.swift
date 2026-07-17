import Foundation
import Observation
import UIKit

/// Scan a W-2, confirm what the model read, then save (M89 over M73/M76).
///
/// The scan NEVER saves. Its output is a set of candidates that land in an
/// editable form, and only `addEarner()` writes anything — the same
/// confirm-before-save contract the dashboard states in its copy, because a
/// vision model misreading Box 1 must not become the household's ground truth.
@MainActor
@Observable
final class W2ScanViewModel {
    struct Form: Equatable {
        var label = ""
        var year: Int?
        var wages: Decimal?
        var withheld: Decimal?
    }

    private(set) var isScanning = false
    private(set) var isSaving = false
    private(set) var didSave = false
    /// What the model says it read, verbatim — shown so the user can judge the
    /// scan rather than trust it.
    private(set) var scanNote: String?
    var form = Form()
    var errorMessage: String?

    var canSave: Bool {
        !form.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isSaving
    }

    private let api: IncomeAPI

    init(api: IncomeAPI) {
        self.api = api
    }

    func scan(_ image: UIImage) async {
        guard !isScanning else { return }
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            errorMessage = "That photo couldn't be processed."
            return
        }
        isScanning = true
        defer { isScanning = false }
        do {
            let attachment = try AttachmentTranscoder.image(from: data, displayName: "W-2")
            apply(try await api.scanW2(attachment))
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    /// A pasted or picked PDF W-2 (M114) — same confirm-before-save contract.
    func scan(pdfData: Data) async {
        guard !isScanning else { return }
        isScanning = true
        defer { isScanning = false }
        do {
            let attachment = try AttachmentTranscoder.pdf(from: pdfData, displayName: "W-2")
            apply(try await api.scanW2(attachment))
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    /// Prefill only what the scan actually found, and never overwrite something
    /// the user already typed — their correction outranks the model's reading.
    func apply(_ result: Components.Schemas.W2ScanResult) {
        if let year = result.year { form.year = year }
        if let wages = result.wagesMinor { form.wages = Self.dollars(wages) }
        if let withheld = result.federalWithheldMinor { form.withheld = Self.dollars(withheld) }
        if let employer = result.employer,
            form.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        {
            form.label = employer
        }
        scanNote = result.note
    }

    func addEarner() async {
        guard canSave else { return }
        isSaving = true
        defer { isSaving = false }
        let request = Components.Schemas.IncomeEarnerCreateRequest(
            label: form.label.trimmingCharacters(in: .whitespacesAndNewlines),
            w2Year: form.year,
            w2WagesMinor: form.wages.map(Self.minorUnits),
            w2WithheldMinor: form.withheld.map(Self.minorUnits)
        )
        do {
            try await api.createEarner(request)
            didSave = true
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    /// Money crosses the contract in minor units (M2). Rounded through Decimal,
    /// not Double — binary floating point turns $4,412.35 into 441234 often
    /// enough to matter on a tax figure.
    static func minorUnits(_ dollars: Decimal) -> Int {
        var value = dollars * 100
        var rounded = Decimal()
        NSDecimalRound(&rounded, &value, 0, .plain)
        return NSDecimalNumber(decimal: rounded).intValue
    }

    static func dollars(_ minor: Int) -> Decimal {
        Decimal(minor) / 100
    }

    private static func describe(_ error: Error) -> String {
        if let incomeError = error as? IncomeAPIError {
            return incomeError.errorDescription ?? "\(incomeError)"
        }
        return ChatViewModel.describe(error)
    }
}
