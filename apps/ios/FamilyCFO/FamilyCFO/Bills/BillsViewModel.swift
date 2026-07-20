import Foundation
import Observation
import UIKit

/// The review queues (M90): recurring-bill suggestions to confirm/dismiss, and
/// unclassified deposits to mark income / not-income. Every action is optimistic
/// — the item leaves its queue at once — but if the server refuses it comes back
/// in place, since a queue that hides an item the box still lists is a lie.
///
/// `pendingCount` drives the tab badge; because this is the SAME view model the
/// tab holds and the screen mutates, the badge updates the moment an item is
/// cleared, with no extra fetch.
@MainActor
@Observable
final class BillsViewModel {
    private(set) var billSuggestions: [Components.Schemas.BillSuggestion] = []
    private(set) var bills: [Components.Schemas.Bill] = []
    /// The payment timeline (M111): the tab's primary view. nil until first load.
    private(set) var timeline: Components.Schemas.PaymentTimelineResponse?
    /// Recurring obligations on liability accounts — loans, leases, 401(k) loans (M106).
    private(set) var obligations: [Components.Schemas.AccountObligation] = []
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var deposits: [Components.Schemas.IncomeAnalysisTransaction] = []
    private(set) var isLoading = false
    private(set) var isSyncing = false
    private(set) var isScanning = false
    var errorMessage: String?
    /// A brief note after a sync, e.g. "Imported 12 transactions".
    var syncResult: String?

    /// The badge only counts things that NEED a decision — suggestions and
    /// unclassified deposits — not the current bills, which are just informational.
    var pendingCount: Int { billSuggestions.count + deposits.count }

    private let api: BillsAPI

    init(api: BillsAPI) {
        self.api = api
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let suggestions = api.billSuggestions()
            async let current = api.bills()
            async let obligated = api.obligations()
            async let cats = api.categories()
            async let dep = api.unclassifiedDeposits()
            async let line = api.paymentTimeline()
            billSuggestions = try await suggestions
            bills = try await current
            obligations = try await obligated
            categories = try await cats
            deposits = try await dep
            timeline = try await line
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Bills grouped by category name for the sectioned list (M96): each named
    /// category with its bills, then "Other" for the uncategorized, so
    /// subscriptions land under Subscriptions. Sections are alphabetical, with
    /// "Other" last.
    var billsByCategory: [(name: String, bills: [Components.Schemas.Bill])] {
        var groups: [String: [Components.Schemas.Bill]] = [:]
        for bill in bills {
            groups[bill.categoryName ?? "Other", default: []].append(bill)
        }
        return groups
            .sorted { lhs, rhs in
                if lhs.key == "Other" { return false }
                if rhs.key == "Other" { return true }
                return lhs.key.localizedCaseInsensitiveCompare(rhs.key) == .orderedAscending
            }
            .map { (name: $0.key, bills: $0.value) }
    }

    /// The timeline grouped for display (M111): Overdue → Due soon → No due date →
    /// Paid this cycle → Upcoming. Empty groups are dropped.
    var timelineSections: [(title: String, items: [Components.Schemas.PaymentTimelineItem])] {
        guard let timeline else { return [] }
        let order: [(Components.Schemas.PaymentTimelineItem.StatusPayload, String)] = [
            (.overdue, "Overdue"),
            (.dueSoon, "Due soon"),
            (.noDate, "No due date yet"),
            (.paid, "Paid this cycle"),
            (.upcoming, "Upcoming"),
        ]
        return order.compactMap { status, title in
            let items = timeline.items.filter { $0.status == status }
            return items.isEmpty ? nil : (title: title, items: items)
        }
    }

    /// One rendered section of the Bills tab: a title with the hand-entered bills
    /// filed under it and/or the account obligations that share that title. Bills
    /// and obligations that carry the SAME name (e.g. a "Loans" category and the
    /// loan/mortgage accounts) collapse into a single section instead of two
    /// identically-titled ones (M110 bugfix).
    struct BillSection: Identifiable {
        var title: String
        var bills: [Components.Schemas.Bill]
        var obligations: [Components.Schemas.AccountObligation]
        /// The account-obligation explainer, shown only when the section has any.
        var obligationFooter: String?
        var id: String { title }
    }

    /// The Bills-tab sections in display order: category-grouped bills first (with
    /// any same-named obligation section merged in), then the remaining
    /// obligation-only sections (Leases, Payroll-deducted, and Loans when no
    /// "Loans" category exists).
    var billSections: [BillSection] {
        var remaining = obligationSections
        func takeObligations(named title: String)
            -> (items: [Components.Schemas.AccountObligation], footer: String?)?
        {
            guard let i = remaining.firstIndex(where: { $0.title == title }) else { return nil }
            let section = remaining.remove(at: i)
            return (section.items, Self.obligationFooter(for: section.title))
        }

        var sections: [BillSection] = billsByCategory.map { group in
            let merged = takeObligations(named: group.name)
            return BillSection(
                title: group.name, bills: group.bills,
                obligations: merged?.items ?? [], obligationFooter: merged?.footer)
        }
        // Whatever obligation sections weren't merged into a bill category.
        sections += remaining.map {
            BillSection(
                title: $0.title, bills: [], obligations: $0.items,
                obligationFooter: Self.obligationFooter(for: $0.title))
        }
        return sections
    }

    static func obligationFooter(for title: String) -> String {
        title == "Payroll-deducted"
            ? "Managed on your accounts — shown here for the full picture. These come out of your paycheck, so safe-to-spend doesn't reserve them again."
            : "Managed on your accounts — shown here so every recurring payment is in one place. Safe-to-spend already reserves these."
    }

    /// Account obligations grouped into display sections (M106): Loans (mortgage +
    /// loans), Leases, and Payroll-deducted (401k loans). Highest payment first.
    var obligationSections: [(title: String, items: [Components.Schemas.AccountObligation])] {
        func items(
            _ kinds: [Components.Schemas.AccountObligation.KindPayload]
        ) -> [Components.Schemas.AccountObligation] {
            obligations
                .filter { kinds.contains($0.kind) }
                .sorted { $0.amount.amountMinor > $1.amount.amountMinor }
        }
        var sections: [(String, [Components.Schemas.AccountObligation])] = []
        let loans = items([.mortgage, .loan])
        if !loans.isEmpty { sections.append(("Loans", loans)) }
        let leases = items([.lease])
        if !leases.isEmpty { sections.append(("Leases", leases)) }
        let payroll = items([.retirementLoan])
        if !payroll.isEmpty { sections.append(("Payroll-deducted", payroll)) }
        return sections.map { (title: $0.0, items: $0.1) }
    }

    /// Delete a category from the shared picker (long-press) — the server
    /// un-categorizes its transactions and any bills; reload to reflect it.
    func deleteCategory(id: String) async {
        do {
            try await api.deleteCategory(id: id)
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func setBillCategory(_ bill: Components.Schemas.Bill, to category: Components.Schemas.Category) async {
        do {
            let alsoFiled = try await api.setBillCategory(id: bill.id, categoryID: category.id)
            // M96 rule: filing the bill also filed its matching transactions —
            // tell the user, so the propagation isn't invisible.
            syncResult =
                alsoFiled > 0
                ? "Filed \(bill.name) and \(alsoFiled) matching transaction\(alsoFiled == 1 ? "" : "s") under \(category.name)."
                : "Filed \(bill.name) under \(category.name)."
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    // MARK: Current bills

    func addBill(
        name: String,
        amountMinor: Int64,
        currency: String,
        frequency: Components.Schemas.RecurringFrequency,
        nextDueDate: String?,
        categoryID: String? = nil
    ) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, amountMinor > 0 else { return }
        let request = Components.Schemas.BillCreateRequest(
            name: trimmed,
            amount: .init(amountMinor: amountMinor, currency: currency),
            frequency: frequency,
            nextDueDate: nextDueDate,
            categoryId: categoryID)
        do {
            try await api.createBill(request)
            await load()  // pull the created bill back with its server id
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Read a photographed bill into candidate values for the add form. Returns
    /// nil (and sets errorMessage) on failure so the user can still type by hand.
    func scanBill(_ image: UIImage) async -> Components.Schemas.BillScanResult? {
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            errorMessage = "That photo couldn't be processed."
            return nil
        }
        return await scan { try AttachmentTranscoder.image(from: data, displayName: "Bill") }
    }

    /// Read a chosen file (a PDF or image bill) into candidate values.
    func scanBill(fileData: Data, isPDF: Bool) async -> Components.Schemas.BillScanResult? {
        await scan {
            isPDF
                ? try AttachmentTranscoder.pdf(from: fileData, displayName: "Bill")
                : try AttachmentTranscoder.image(from: fileData, displayName: "Bill")
        }
    }

    private func scan(
        _ makeAttachment: () throws -> ChatAttachment
    ) async -> Components.Schemas.BillScanResult? {
        guard !isScanning else { return nil }
        isScanning = true
        defer { isScanning = false }
        do {
            let result = try await api.scanBill(makeAttachment())
            errorMessage = nil
            return result
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return nil
        }
    }

    /// Edit an existing bill's fields. Reloads on success so the row reflects the
    /// server's copy; the server records it as an undoable action.
    func editBill(
        _ bill: Components.Schemas.Bill,
        name: String,
        amountMinor: Int64,
        currency: String,
        frequency: Components.Schemas.RecurringFrequency,
        nextDueDate: String?,
        categoryID: String?
    ) async {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, amountMinor > 0 else { return }
        do {
            try await api.updateBill(
                id: bill.id, name: trimmed, amountMinor: amountMinor, currency: currency,
                frequency: frequency, nextDueDate: nextDueDate, categoryID: categoryID)
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func deleteBill(_ bill: Components.Schemas.Bill) async {
        guard let index = bills.firstIndex(where: { $0.id == bill.id }) else { return }
        bills.remove(at: index)
        do {
            try await api.deleteBill(id: bill.id)
            errorMessage = nil
        } catch {
            bills.insert(bill, at: min(index, bills.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }

    // MARK: Bank sync

    /// Re-pull transactions from linked accounts, then reload so new bill
    /// suggestions and deposits appear.
    func sync() async {
        guard !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        syncResult = nil
        do {
            let totals = try await api.syncAllTransactions()
            syncResult = Self.syncSummary(totals)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Human summary of a sync: what came in, and what the app filed so the user
    /// doesn't have to (M96 "say what you did").
    static func syncSummary(_ totals: SyncTotals) -> String {
        guard totals.imported > 0 else { return "Already up to date." }
        var message = "Imported \(totals.imported) new transaction\(totals.imported == 1 ? "" : "s")."
        var filed: [String] = []
        if totals.autoCategorized > 0 { filed.append("categorized \(totals.autoCategorized)") }
        if totals.transfersFiled > 0 { filed.append("filed \(totals.transfersFiled) as transfers") }
        if !filed.isEmpty { message += " Auto-\(filed.joined(separator: ", ")) for you." }
        return message
    }

    // MARK: Bill suggestions

    func confirmBill(_ suggestion: Components.Schemas.BillSuggestion) async {
        await actOnBill(suggestion) { try await self.api.confirmBill(suggestion) }
    }

    func dismissBill(_ suggestion: Components.Schemas.BillSuggestion) async {
        await actOnBill(suggestion) { try await self.api.dismissBill(merchantKey: suggestion.merchantKey) }
    }

    private func actOnBill(
        _ suggestion: Components.Schemas.BillSuggestion,
        _ action: () async throws -> Void
    ) async {
        guard let index = billSuggestions.firstIndex(where: { $0.merchantKey == suggestion.merchantKey })
        else { return }
        billSuggestions.remove(at: index)
        do {
            try await action()
            errorMessage = nil
        } catch {
            billSuggestions.insert(suggestion, at: min(index, billSuggestions.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }

    // MARK: Unclassified deposits

    func markIncome(_ deposit: Components.Schemas.IncomeAnalysisTransaction) async {
        await actOnDeposit(deposit, verdict: .include)
    }

    func markNotIncome(_ deposit: Components.Schemas.IncomeAnalysisTransaction) async {
        await actOnDeposit(deposit, verdict: .exclude)
    }

    private func actOnDeposit(
        _ deposit: Components.Schemas.IncomeAnalysisTransaction,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async {
        guard let index = deposits.firstIndex(where: { $0.transactionId == deposit.transactionId })
        else { return }
        deposits.remove(at: index)
        do {
            try await api.setDepositVerdict(transactionID: deposit.transactionId, verdict: verdict)
            errorMessage = nil
        } catch {
            deposits.insert(deposit, at: min(index, deposits.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
