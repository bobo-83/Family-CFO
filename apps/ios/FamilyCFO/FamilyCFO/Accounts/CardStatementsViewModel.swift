import Foundation
import Observation
import UIKit

/// #11: the statement cycles recorded against one credit card.
///
/// A synced card balance is a running total — it includes everything charged
/// since the cycle closed, so it can never say what the card actually asks for
/// this month. A recorded statement can: it's the EXACT amount, with the day
/// it's due. Nothing here is inferred, and the scan never saves — it only
/// offers candidates the user confirms.
@MainActor
@Observable
final class CardStatementsViewModel {
    private let api: AccountsAPI
    let account: Components.Schemas.Account

    private(set) var statements: [Components.Schemas.CardStatement] = []
    private(set) var isLoading = false
    private(set) var isScanning = false
    private(set) var isSaving = false
    var errorMessage: String?

    /// #25: the transaction table the last scan read, held UNSAVED. Storing it
    /// is a separate, explicit step — and it replaces the previous read.
    private(set) var scannedLines: [Components.Schemas.CardStatementScanLine] = []
    /// The recorded cycle those scanned lines can hang off. Lines need a
    /// statement to belong to, so the offer only appears once one exists.
    private(set) var scannedLinesStatementID: String?
    private(set) var isStoringLines = false

    init(api: AccountsAPI, account: Components.Schemas.Account) {
        self.api = api
        self.account = account
    }

    var currency: String { account.balance.currency }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            // Newest cycle first — the one you're about to pay leads.
            statements = try await api.cardStatements(accountID: account.id)
                .sorted { $0.dueDate > $1.dueDate }
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    /// Record (or correct) a cycle, then refresh. Recording the same due date
    /// again updates that cycle instead of stacking a second obligation for the
    /// same money, so a re-scan of the same statement is safe.
    @discardableResult
    func record(_ draft: Draft) async -> Bool {
        guard !isSaving else { return false }
        isSaving = true
        defer { isSaving = false }
        do {
            try await api.recordCardStatement(
                CardStatementDraft(
                    accountID: account.id,
                    currency: currency,
                    statementBalanceMinor: Self.minor(draft.amount),
                    dueDate: LoanDate.iso(from: draft.dueDate),
                    minimumDueMinor: draft.hasMinimum ? Self.minor(draft.minimum) : nil,
                    periodStart: draft.hasPeriod ? LoanDate.iso(from: draft.periodStart) : nil,
                    periodEnd: draft.hasPeriod ? LoanDate.iso(from: draft.periodEnd) : nil))
            errorMessage = nil
            await load()
            // #25: the cycle now exists, so the lines the same scan read have
            // something to be stored against. Same card + due date is one
            // cycle, which is exactly how the just-saved one is found again.
            if !scannedLines.isEmpty {
                let dueDate = LoanDate.iso(from: draft.dueDate)
                scannedLinesStatementID = statements.first { $0.dueDate == dueDate }?.id
            }
            return true
        } catch {
            errorMessage = Self.describe(error)
            return false
        }
    }

    /// Mark the cycle settled today, or clear the mark when `paid` is false.
    func setPaid(_ statement: Components.Schemas.CardStatement, paid: Bool) async {
        do {
            try await api.markCardStatementPaid(
                id: statement.id, paidAt: paid ? LoanDate.iso(from: Date()) : nil)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    func delete(_ statement: Components.Schemas.CardStatement) async {
        do {
            try await api.deleteCardStatement(id: statement.id)
            statements.removeAll { $0.id == statement.id }
            // Lines can't outlive the cycle they were read from.
            if scannedLinesStatementID == statement.id { discardScannedLines() }
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    // MARK: - #25: the statement's own line items

    /// The check for one cycle: which of its charges the synced ledger explains.
    /// Reading it takes no write right — anyone who can see the cycle can see
    /// what it does and doesn't account for.
    func reconciliation(
        for statement: Components.Schemas.CardStatement
    ) -> StatementReconciliationViewModel {
        StatementReconciliationViewModel(api: api, statement: statement)
    }

    /// True once a scan has read a transaction table that hasn't been stored.
    var hasScannedLines: Bool { !scannedLines.isEmpty }

    /// The offer can only be taken once the cycle those lines belong to exists.
    var canStoreScannedLines: Bool { scannedLinesStatementID != nil && hasScannedLines }

    /// Store the scanned table against its cycle. This REPLACES whatever was
    /// read for that statement before — never a second copy of the same charges.
    func storeScannedLines() async {
        guard let statementID = scannedLinesStatementID, !scannedLines.isEmpty, !isStoringLines
        else { return }
        isStoringLines = true
        defer { isStoringLines = false }
        let lines = scannedLines.compactMap { line -> Components.Schemas.StatementLineInput? in
            // A row with no date can't be matched against anything, so it would
            // read as a permanent gap — drop it rather than invent a day.
            guard let occurredOn = line.occurredOn else { return nil }
            return .init(
                occurredOn: occurredOn,
                description: line.description,
                // Already in the ledger's convention: negative is a charge.
                amount: .init(amountMinor: Int64(line.amountMinor), currency: currency))
        }
        do {
            _ = try await api.replaceStatementLines(statementID: statementID, lines: lines)
            discardScannedLines()
            errorMessage = nil
            await load()
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    /// Throw the unstored read away — the ledger is untouched either way.
    func discardScannedLines() {
        scannedLines = []
        scannedLinesStatementID = nil
    }

    /// Read a photographed statement into candidates. Returns nil (and sets
    /// `errorMessage`) on failure, so the figures can still be typed by hand.
    func scan(_ image: UIImage) async -> Components.Schemas.CardStatementScanResult? {
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            errorMessage = String(localized: "That photo couldn't be processed.")
            return nil
        }
        return await scan { try AttachmentTranscoder.image(from: data, displayName: "Statement") }
    }

    func scan(fileData: Data, isPDF: Bool) async -> Components.Schemas.CardStatementScanResult? {
        await scan {
            isPDF
                ? try AttachmentTranscoder.pdf(from: fileData, displayName: "Statement")
                : try AttachmentTranscoder.image(from: fileData, displayName: "Statement")
        }
    }

    private func scan(
        _ makeAttachment: () throws -> ChatAttachment
    ) async -> Components.Schemas.CardStatementScanResult? {
        guard !isScanning else { return nil }
        isScanning = true
        defer { isScanning = false }
        do {
            let result = try await api.scanCardStatement(makeAttachment())
            // #25: the transaction table rides along with the summary. It is
            // held here, unstored, until the cycle exists and the household
            // says to keep it — a re-scan replaces this read, never doubles it.
            scannedLines = (result.lines ?? []).filter { $0.occurredOn != nil }
            scannedLinesStatementID = nil
            errorMessage = nil
            return result
        } catch {
            errorMessage = Self.describe(error)
            return nil
        }
    }

    /// "Due Aug 12", or the day it was settled once it's paid.
    static func dueLine(_ statement: Components.Schemas.CardStatement) -> String {
        let due = BillsView.shortDate(statement.dueDate)
        guard let paidAt = statement.paidAt else {
            return String(localized: "Due \(due)")
        }
        return String(localized: "Paid \(BillsView.shortDate(paidAt)) · was due \(due)")
    }

    /// The second line: the minimum the card will accept, and the cycle the
    /// statement covers — only what was actually recorded.
    static func detailLine(_ statement: Components.Schemas.CardStatement) -> String? {
        var parts: [String] = []
        if let minimum = statement.minimumDue {
            parts.append(String(localized: "Minimum \(minimum.formattedExact)"))
        }
        if let start = statement.periodStart, let end = statement.periodEnd {
            parts.append(
                String(localized: "Cycle \(BillsView.shortDate(start))–\(BillsView.shortDate(end))"))
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private static func minor(_ amount: Double) -> Int64 { Int64((amount * 100).rounded()) }

    /// The scan errors carry their own words; everything else falls back to the
    /// shared describer.
    private static func describe(_ error: Error) -> String {
        if let scanError = error as? CardStatementScanError {
            return scanError.errorDescription ?? "\(scanError)"
        }
        return ChatViewModel.describe(error)
    }
}

extension CardStatementsViewModel {
    /// What the add-statement sheet holds. A value type so the scan's
    /// prefill rules are exercised without a view — and so it's obvious that a
    /// scan only fills this in: saving is a separate, explicit step.
    struct Draft: Equatable {
        var amount: Double = 0
        var dueDate = Date()
        /// True once the user picked a date themselves — their choice outranks
        /// the model's reading, so a later scan leaves it alone.
        var dueDateTouched = false
        var hasMinimum = false
        var minimum: Double = 0
        var hasPeriod = false
        var periodStart = Date()
        var periodEnd = Date()

        /// A cycle with no amount says nothing — there'd be no exact figure.
        var canSave: Bool { amount > 0 }

        /// Prefill only what the scan found and the user hasn't already set.
        /// Nothing is saved here; the user confirms every figure first.
        mutating func apply(_ result: Components.Schemas.CardStatementScanResult) {
            if let balance = result.statementBalanceMinor, amount == 0 {
                amount = Double(balance) / 100
            }
            if let minimum = result.minimumDueMinor, !hasMinimum {
                hasMinimum = true
                self.minimum = Double(minimum) / 100
            }
            if let due = LoanDate.date(from: result.dueDate), !dueDateTouched {
                dueDate = due
            }
            if !hasPeriod, let start = LoanDate.date(from: result.periodStart),
                let end = LoanDate.date(from: result.periodEnd)
            {
                hasPeriod = true
                periodStart = start
                periodEnd = end
            }
        }
    }
}
