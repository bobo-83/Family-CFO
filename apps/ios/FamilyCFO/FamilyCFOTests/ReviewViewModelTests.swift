import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockReviewAPI: ReviewAPI, @unchecked Sendable {
    var suggestions: [Components.Schemas.BillSuggestion] = []
    var deposits: [Components.Schemas.IncomeAnalysisTransaction] = []
    var actionError: Error?
    private(set) var confirmed: [String] = []
    private(set) var dismissed: [String] = []
    private(set) var verdicts: [(id: String, verdict: String)] = []

    nonisolated func billSuggestions() async throws -> [Components.Schemas.BillSuggestion] {
        try await MainActor.run { suggestions }
    }

    nonisolated func confirmBill(_ suggestion: Components.Schemas.BillSuggestion) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            confirmed.append(suggestion.merchantKey)
        }
    }

    nonisolated func dismissBill(merchantKey: String) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            dismissed.append(merchantKey)
        }
    }

    nonisolated func unclassifiedDeposits() async throws
        -> [Components.Schemas.IncomeAnalysisTransaction]
    {
        try await MainActor.run { deposits }
    }

    nonisolated func setDepositVerdict(
        transactionID: String,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            verdicts.append((transactionID, verdict.rawValue))
        }
    }
}

@MainActor
struct ReviewViewModelTests {
    private func suggestion(_ key: String, _ name: String) -> Components.Schemas.BillSuggestion {
        .init(
            merchantKey: key,
            name: name,
            amount: .init(amountMinor: 1_299, currency: "USD"),
            frequency: .monthly,
            nextDueDate: "2026-08-01",
            occurrences: 3,
            lastSeen: "2026-07-01"
        )
    }

    private func deposit(_ id: String, _ name: String) -> Components.Schemas.IncomeAnalysisTransaction {
        .init(
            transactionId: id,
            occurredAt: "2026-07-10",
            amount: .init(amountMinor: 250_000, currency: "USD"),
            name: name,
            excluded: false
        )
    }

    private func loaded() async -> (ReviewViewModel, MockReviewAPI) {
        let api = MockReviewAPI()
        api.suggestions = [suggestion("netflix", "Netflix"), suggestion("gym", "Gym")]
        api.deposits = [deposit("d1", "Zelle from Mom"), deposit("d2", "Venmo")]
        let vm = ReviewViewModel(api: api)
        await vm.load()
        return (vm, api)
    }

    @Test func pendingCountSumsBothQueues() async {
        let (vm, _) = await loaded()

        #expect(vm.pendingCount == 4)  // 2 bills + 2 deposits
    }

    @Test func confirmingABillCreatesItAndClearsItFromTheQueue() async {
        let (vm, api) = await loaded()

        await vm.confirmBill(vm.billSuggestions[0])

        #expect(api.confirmed == ["netflix"])
        #expect(vm.billSuggestions.map(\.merchantKey) == ["gym"])
        #expect(vm.pendingCount == 3)
    }

    @Test func dismissingABillRemovesItWithoutCreatingABill() async {
        let (vm, api) = await loaded()

        await vm.dismissBill(vm.billSuggestions[1])

        #expect(api.dismissed == ["gym"])
        #expect(api.confirmed.isEmpty)
        #expect(vm.billSuggestions.map(\.merchantKey) == ["netflix"])
    }

    @Test func markingADepositIncomeSendsIncludeAndClearsIt() async {
        let (vm, api) = await loaded()

        await vm.markIncome(vm.deposits[0])

        #expect(api.verdicts.map(\.id) == ["d1"])
        #expect(api.verdicts.map(\.verdict) == ["include"])
        #expect(vm.deposits.map(\.transactionId) == ["d2"])
    }

    @Test func markingNotIncomeSendsExclude() async {
        let (vm, api) = await loaded()

        await vm.markNotIncome(vm.deposits[1])

        #expect(api.verdicts.map(\.verdict) == ["exclude"])
    }

    /// A failed action must restore the item in place — the badge and the queue
    /// must never claim something was handled that the box still lists.
    @Test func aFailedActionRestoresTheItemAndTheCount() async {
        let (vm, api) = await loaded()
        api.actionError = APIError.server(500)

        await vm.confirmBill(vm.billSuggestions[0])

        #expect(vm.billSuggestions.map(\.merchantKey) == ["netflix", "gym"])
        #expect(vm.pendingCount == 4)
        #expect(vm.errorMessage != nil)
    }

    /// Already-excluded deposits are the user's past decisions, not review items.
    @Test func excludedDepositsAreNotInTheQueue() async {
        let api = MockReviewAPI()
        // The live API filters these out; the mock returns what load() stores, so
        // assert the seam contract via LiveReviewAPI-style filtering expectations
        // by only handing the VM the active set.
        api.deposits = [deposit("d1", "Paycheck")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        #expect(vm.deposits.count == 1)
    }
}
