import Foundation
import Observation
import UIKit

@MainActor
@Observable
final class DebtsViewModel {
    private(set) var loans: [Components.Schemas.Account] = []
    private(set) var isLoading = false
    private(set) var isSaving = false
    private(set) var isScanning = false
    var errorMessage: String?

    private let api: DebtsAPI
    /// Currency for new loans — the household's, learned from existing accounts.
    private(set) var currency = "USD"

    init(api: DebtsAPI) {
        self.api = api
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            loans = try await api.loans()
            if let first = loans.first { currency = first.balance.currency }
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Returns true on success so the form can dismiss.
    func addLoan(_ draft: LoanDraft) async -> Bool {
        guard !isSaving else { return false }
        isSaving = true
        defer { isSaving = false }
        do {
            try await api.addLoan(draft)
            await load()
            return true
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return false
        }
    }

    func updateLoan(id: String, _ draft: LoanDraft) async -> Bool {
        guard !isSaving else { return false }
        isSaving = true
        defer { isSaving = false }
        do {
            try await api.updateLoan(id: id, draft)
            await load()
            return true
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return false
        }
    }

    /// Read a photographed statement into candidate values. Returns nil (and sets
    /// errorMessage) on failure, so the user can still type the numbers by hand.
    func scanStatement(_ image: UIImage) async -> Components.Schemas.LoanScanResult? {
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            errorMessage = "That photo couldn't be processed."
            return nil
        }
        return await scan { try AttachmentTranscoder.image(from: data, displayName: "Statement") }
    }

    /// Read a chosen file (a PDF or image statement) into candidate values.
    func scanStatement(fileData: Data, isPDF: Bool) async -> Components.Schemas.LoanScanResult? {
        await scan {
            isPDF
                ? try AttachmentTranscoder.pdf(from: fileData, displayName: "Statement")
                : try AttachmentTranscoder.image(from: fileData, displayName: "Statement")
        }
    }

    private func scan(
        _ makeAttachment: () throws -> ChatAttachment
    ) async -> Components.Schemas.LoanScanResult? {
        guard !isScanning else { return nil }
        isScanning = true
        defer { isScanning = false }
        do {
            let result = try await api.scanStatement(makeAttachment())
            errorMessage = nil
            return result
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return nil
        }
    }

    func deleteLoan(_ loan: Components.Schemas.Account) async {
        do {
            try await api.deleteLoan(id: loan.id)
            loans.removeAll { $0.id == loan.id }
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Total still owed across all loans, for the header.
    var totalOwed: Int64 {
        loans.reduce(0) { $0 + max(0, -$1.balance.amountMinor) }
    }

    /// Total monthly payment that actually claims bank cash (counts against
    /// safe-to-spend). 401(k) loans are payroll-deducted, so they're excluded —
    /// matching how the server computes safe-to-spend.
    var totalMonthly: Int64 {
        loans.reduce(0) { total, loan in
            loan._type == ._401kLoan ? total : total + (loan.minimumPayment?.amountMinor ?? 0)
        }
    }
}

/// ISO date <-> Date helpers for a loan's maturity, plus the months/payments left.
enum LoanDate {
    private static let iso: DateFormatter = {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .iso8601)
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    static func date(from iso: String?) -> Date? { iso.flatMap { Self.iso.date(from: $0) } }
    static func iso(from date: Date) -> String { Self.iso.string(from: date) }

    /// "Oct 2026" for the row/summary.
    static func label(_ iso: String?) -> String? {
        guard let date = date(from: iso) else { return nil }
        return date.formatted(.dateTime.month(.abbreviated).year())
    }

    /// Whole months from today to maturity, rounded up (≈ payments remaining).
    static func monthsLeft(_ iso: String?) -> Int? {
        guard let maturity = date(from: iso) else { return nil }
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        let comps = cal.dateComponents([.month, .day], from: today, to: cal.startOfDay(for: maturity))
        guard let months = comps.month else { return nil }
        if months < 0 { return 0 }
        return months + ((comps.day ?? 0) > 0 ? 1 : 0)
    }

    /// The maturity implied by "N monthly payments remaining" (M115): N months
    /// from today. The inverse of `monthsLeft`, so entering either representation
    /// stores the same single source of truth (the date).
    static func dateAfter(payments: Int, from today: Date = Date()) -> Date {
        let cal = Calendar.current
        let start = cal.startOfDay(for: today)
        return cal.date(byAdding: .month, value: max(payments, 0), to: start) ?? start
    }
}

extension Components.Schemas.AccountType {
    /// Human label for the loan-type picker and rows.
    var loanLabel: String {
        switch self {
        case .mortgage: return "Mortgage"
        case .autoLoan: return "Auto loan"
        case .studentLoan: return "Student loan"
        case ._401kLoan: return "401(k) loan"
        case .otherLiability: return "Other loan"
        default: return "Loan"
        }
    }
}
