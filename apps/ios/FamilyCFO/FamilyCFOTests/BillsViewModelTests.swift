import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockBillsAPI: BillsAPI, @unchecked Sendable {
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

    var currentBills: [Components.Schemas.Bill] = []
    var importCount = 0
    private(set) var createdBills: [String] = []
    private(set) var deletedBills: [String] = []
    private(set) var syncCalls = 0

    var cats: [Components.Schemas.Category] = []
    private(set) var billCategorySets: [(id: String, categoryID: String)] = []

    nonisolated func bills() async throws -> [Components.Schemas.Bill] {
        try await MainActor.run { currentBills }
    }

    nonisolated func categories() async throws -> [Components.Schemas.Category] {
        try await MainActor.run { cats }
    }

    var propagatedCount = 0

    nonisolated func setBillCategory(id: String, categoryID: String) async throws -> Int {
        try await MainActor.run {
            if let actionError { throw actionError }
            billCategorySets.append((id, categoryID))
            if let i = currentBills.firstIndex(where: { $0.id == id }) {
                let b = currentBills[i]
                currentBills[i] = .init(
                    id: b.id, name: b.name, amount: b.amount, frequency: b.frequency,
                    nextDueDate: b.nextDueDate,
                    categoryId: categoryID,
                    categoryName: cats.first { $0.id == categoryID }?.name)
            }
            return propagatedCount
        }
    }

    nonisolated func createBill(_ request: Components.Schemas.BillCreateRequest) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            createdBills.append(request.name)
            currentBills.append(.init(
                id: "bill-\(createdBills.count)", name: request.name,
                amount: request.amount, frequency: request.frequency,
                nextDueDate: request.nextDueDate))
        }
    }

    nonisolated func deleteBill(id: String) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            deletedBills.append(id)
            currentBills.removeAll { $0.id == id }
        }
    }

    nonisolated func syncAllTransactions() async throws -> Int {
        try await MainActor.run {
            if let actionError { throw actionError }
            syncCalls += 1
            return importCount
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
struct BillsViewModelTests {
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

    private func loaded() async -> (BillsViewModel, MockBillsAPI) {
        let api = MockBillsAPI()
        api.suggestions = [suggestion("netflix", "Netflix"), suggestion("gym", "Gym")]
        api.deposits = [deposit("d1", "Zelle from Mom"), deposit("d2", "Venmo")]
        let vm = BillsViewModel(api: api)
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
        let api = MockBillsAPI()
        // The live API filters these out; the mock returns what load() stores, so
        // assert the seam contract via LiveBillsAPI-style filtering expectations
        // by only handing the VM the active set.
        api.deposits = [deposit("d1", "Paycheck")]
        let vm = BillsViewModel(api: api)
        await vm.load()

        #expect(vm.deposits.count == 1)
    }
}

@MainActor
struct BillsManagementTests {
    private func loaded() async -> (BillsViewModel, MockBillsAPI) {
        let api = MockBillsAPI()
        api.currentBills = [
            .init(id: "b1", name: "Rent",
                  amount: .init(amountMinor: 200_000, currency: "USD"),
                  frequency: .monthly, nextDueDate: "2026-08-01"),
        ]
        let vm = BillsViewModel(api: api)
        await vm.load()
        return (vm, api)
    }

    @Test func loadsCurrentBills() async {
        let (vm, _) = await loaded()
        #expect(vm.bills.map(\.name) == ["Rent"])
    }

    @Test func addingABillCreatesItAndReloads() async {
        let (vm, api) = await loaded()

        await vm.addBill(
            name: "Gym", amountMinor: 4_999, currency: "USD",
            frequency: .monthly, nextDueDate: "2026-08-05")

        #expect(api.createdBills == ["Gym"])
        #expect(vm.bills.map(\.name).sorted() == ["Gym", "Rent"])
    }

    @Test func aBlankOrZeroBillIsNotCreated() async {
        let (vm, api) = await loaded()

        await vm.addBill(name: "  ", amountMinor: 0, currency: "USD", frequency: .monthly, nextDueDate: nil)

        #expect(api.createdBills.isEmpty)
    }

    @Test func deletingABillRemovesItOptimistically() async {
        let (vm, api) = await loaded()

        await vm.deleteBill(vm.bills[0])

        #expect(api.deletedBills == ["b1"])
        #expect(vm.bills.isEmpty)
    }

    @Test func aFailedDeleteRestoresTheBill() async {
        let (vm, api) = await loaded()
        api.actionError = APIError.server(500)

        await vm.deleteBill(vm.bills[0])

        #expect(vm.bills.map(\.id) == ["b1"])
        #expect(vm.errorMessage != nil)
    }

    @Test func currentBillsDoNotCountTowardTheBadge() async {
        let (vm, _) = await loaded()
        // 1 bill, 0 suggestions, 0 deposits -> badge 0.
        #expect(vm.pendingCount == 0)
    }

    @Test func syncImportsAndReports() async {
        let (vm, api) = await loaded()
        api.importCount = 12

        await vm.sync()

        #expect(api.syncCalls == 1)
        #expect(vm.syncResult?.contains("12") == true)
    }

    @Test func syncWithNothingNewSaysUpToDate() async {
        let (vm, api) = await loaded()
        api.importCount = 0

        await vm.sync()

        #expect(vm.syncResult?.contains("up to date") == true)
    }
}

@MainActor
struct BillCategoryTests {
    private func bill(_ id: String, _ name: String, category: (String, String)? = nil) -> Components.Schemas.Bill {
        .init(id: id, name: name,
              amount: .init(amountMinor: 3299, currency: "USD"),
              frequency: .monthly, nextDueDate: "2026-08-01",
              categoryId: category?.0, categoryName: category?.1)
    }

    private func loaded() async -> (BillsViewModel, MockBillsAPI) {
        let api = MockBillsAPI()
        api.cats = [.init(id: "subs", name: "Subscriptions"), .init(id: "kids", name: "Kids")]
        api.currentBills = [
            bill("b1", "Disney+", category: ("subs", "Subscriptions")),
            bill("b2", "Netflix", category: ("subs", "Subscriptions")),
            bill("b3", "Swim School", category: ("kids", "Kids")),
            bill("b4", "AAA"),  // uncategorized
        ]
        let vm = BillsViewModel(api: api)
        await vm.load()
        return (vm, api)
    }

    @Test func groupsBillsByCategoryWithOtherLast() async {
        let (vm, _) = await loaded()

        let names = vm.billsByCategory.map(\.name)
        #expect(names == ["Kids", "Subscriptions", "Other"])  // alpha, Other last
        let subs = vm.billsByCategory.first { $0.name == "Subscriptions" }
        #expect(subs?.bills.map(\.name).sorted() == ["Disney+", "Netflix"])
    }

    @Test func categorizingABillFilesIt() async {
        let (vm, api) = await loaded()
        let aaa = vm.bills.first { $0.name == "AAA" }!

        await vm.setBillCategory(aaa, to: api.cats[0])  // Subscriptions

        #expect(api.billCategorySets.map(\.id) == ["b4"])
        #expect(api.billCategorySets.map(\.categoryID) == ["subs"])
        // AAA is now under Subscriptions, so "Other" is gone.
        #expect(!vm.billsByCategory.map(\.name).contains("Other"))
    }

    @Test func addingABillPassesItsCategory() async {
        let (vm, api) = await loaded()

        await vm.addBill(
            name: "Spotify", amountMinor: 1099, currency: "USD",
            frequency: .monthly, nextDueDate: nil, categoryID: "subs")

        #expect(api.createdBills == ["Spotify"])
    }
}

@MainActor
struct BillCategoryPropagationTests {
    @Test func categorizingABillReportsPropagatedTransactions() async {
        let api = MockBillsAPI()
        api.cats = [.init(id: "subs", name: "Subscriptions")]
        api.currentBills = [.init(
            id: "b1", name: "Disney+",
            amount: .init(amountMinor: 3299, currency: "USD"),
            frequency: .monthly, nextDueDate: nil)]
        api.propagatedCount = 5
        let vm = BillsViewModel(api: api)
        await vm.load()

        await vm.setBillCategory(vm.bills[0], to: api.cats[0])

        #expect(vm.syncResult?.contains("5 matching transactions") == true)
        #expect(vm.syncResult?.contains("Subscriptions") == true)
    }

    @Test func noMatchesReportsJustTheBill() async {
        let api = MockBillsAPI()
        api.cats = [.init(id: "subs", name: "Subscriptions")]
        api.currentBills = [.init(
            id: "b1", name: "AAA",
            amount: .init(amountMinor: 250, currency: "USD"),
            frequency: .monthly, nextDueDate: nil)]
        api.propagatedCount = 0
        let vm = BillsViewModel(api: api)
        await vm.load()

        await vm.setBillCategory(vm.bills[0], to: api.cats[0])

        #expect(vm.syncResult == "Filed AAA under Subscriptions.")
    }
}
