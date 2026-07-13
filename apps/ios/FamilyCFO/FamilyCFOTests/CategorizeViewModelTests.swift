import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockCategorizeAPI: CategorizeAPI, @unchecked Sendable {
    var uncategorizedTxns: [Components.Schemas.Transaction] = []
    var cats: [Components.Schemas.Category] = []
    var setError: Error?
    private(set) var setCalls: [(id: String, categoryID: String?)] = []

    nonisolated func uncategorized() async throws -> [Components.Schemas.Transaction] {
        try await MainActor.run { uncategorizedTxns }
    }

    nonisolated func categories() async throws -> [Components.Schemas.Category] {
        try await MainActor.run { cats }
    }

    nonisolated func setCategory(transactionID: String, categoryID: String?) async throws {
        try await MainActor.run {
            setCalls.append((transactionID, categoryID))
            if let setError { throw setError }
        }
    }
}

@MainActor
struct CategorizeViewModelTests {
    private func txn(_ id: String, _ merchant: String, at: String) -> Components.Schemas.Transaction {
        .init(
            id: id,
            accountId: "acct-1",
            occurredAt: at,
            amount: .init(amountMinor: -4_299, currency: "USD"),
            merchant: merchant
        )
    }

    private func category(_ id: String, _ name: String) -> Components.Schemas.Category {
        .init(id: id, name: name)
    }

    private func loaded() async -> (CategorizeViewModel, MockCategorizeAPI) {
        let api = MockCategorizeAPI()
        api.uncategorizedTxns = [
            txn("t1", "Whole Foods", at: "2026-07-13"),
            txn("t2", "Shell", at: "2026-07-12"),
        ]
        api.cats = [category("c1", "Groceries"), category("c2", "Gas")]
        let vm = CategorizeViewModel(api: api)
        await vm.load()
        return (vm, api)
    }

    @Test func loadsUncategorizedNewestFirstAndCategories() async {
        let (vm, _) = await loaded()

        #expect(vm.transactions.map(\.id) == ["t1", "t2"])
        #expect(vm.categories.count == 2)
    }

    @Test func categorizingRemovesTheRowAndCallsTheApi() async {
        let (vm, api) = await loaded()

        await vm.categorize(vm.transactions[0], as: api.cats[0])

        #expect(vm.transactions.map(\.id) == ["t2"])
        #expect(api.setCalls.map(\.id) == ["t1"])
        #expect(api.setCalls.map(\.categoryID) == ["c1"])
        #expect(vm.lastAction?.categoryName == "Groceries")
    }

    /// A row that vanishes while the box still shows it uncategorized is a lie.
    /// On failure it must come back, in its original position.
    @Test func aFailedAssignmentRestoresTheRow() async {
        let (vm, api) = await loaded()
        api.setError = APIError.server(500)

        await vm.categorize(vm.transactions[0], as: api.cats[0])

        #expect(vm.transactions.map(\.id) == ["t1", "t2"])
        #expect(vm.transactions.first?.id == "t1")  // original position
        #expect(vm.errorMessage != nil)
        #expect(vm.lastAction == nil)  // nothing to undo — it didn't happen
    }

    /// Undo clears the category server-side (categoryID nil) and puts the row
    /// back where it was.
    @Test func undoClearsTheCategoryAndRestoresTheRow() async {
        let (vm, api) = await loaded()
        await vm.categorize(vm.transactions[0], as: api.cats[0])  // t1 -> Groceries

        await vm.undoLast()

        #expect(api.setCalls.map(\.id) == ["t1", "t1"])
        #expect(api.setCalls.map(\.categoryID) == ["c1", nil])  // set, then clear
        #expect(vm.transactions.map(\.id) == ["t1", "t2"])  // restored to index 0
        #expect(vm.lastAction == nil)
    }

    @Test func dismissingUndoLeavesTheCategorizationInPlace() async {
        let (vm, api) = await loaded()
        await vm.categorize(vm.transactions[0], as: api.cats[0])

        vm.dismissUndo()

        #expect(vm.lastAction == nil)
        #expect(api.setCalls.map(\.categoryID) == ["c1"])  // no clear call — it stays set
        #expect(vm.transactions.map(\.id) == ["t2"])
    }

    @Test func onlyTheMostRecentActionIsUndoable() async {
        let (vm, api) = await loaded()
        await vm.categorize(vm.transactions[0], as: api.cats[0])  // t1
        await vm.categorize(vm.transactions[0], as: api.cats[1])  // t2

        #expect(vm.lastAction?.transaction.id == "t2")
    }
}
