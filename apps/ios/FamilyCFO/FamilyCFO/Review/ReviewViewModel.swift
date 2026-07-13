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
final class ReviewViewModel {
    private(set) var billSuggestions: [Components.Schemas.BillSuggestion] = []
    private(set) var deposits: [Components.Schemas.IncomeAnalysisTransaction] = []
    private(set) var isLoading = false
    var errorMessage: String?

    var pendingCount: Int { billSuggestions.count + deposits.count }

    private let api: ReviewAPI

    init(api: ReviewAPI) {
        self.api = api
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let bills = api.billSuggestions()
            async let dep = api.unclassifiedDeposits()
            billSuggestions = try await bills
            deposits = try await dep
            errorMessage = nil
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
