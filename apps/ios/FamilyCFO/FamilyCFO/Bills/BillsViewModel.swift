import Foundation
import Observation

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
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var deposits: [Components.Schemas.IncomeAnalysisTransaction] = []
    private(set) var isLoading = false
    private(set) var isSyncing = false
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
            async let cats = api.categories()
            async let dep = api.unclassifiedDeposits()
            billSuggestions = try await suggestions
            bills = try await current
            categories = try await cats
            deposits = try await dep
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

    func setBillCategory(_ bill: Components.Schemas.Bill, to category: Components.Schemas.Category) async {
        do {
            try await api.setBillCategory(id: bill.id, categoryID: category.id)
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
            let imported = try await api.syncAllTransactions()
            syncResult =
                imported == 0
                ? "Already up to date."
                : "Imported \(imported) new transaction\(imported == 1 ? "" : "s")."
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
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
