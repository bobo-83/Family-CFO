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

    var createError: Error?
    private(set) var created: [String] = []
    var nextCreatedId = "new-cat"

    nonisolated func createCategory(name: String) async throws -> Components.Schemas.Category {
        try await MainActor.run {
            if let createError { throw createError }
            created.append(name)
            return .init(id: nextCreatedId, name: name)
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

@MainActor
struct CategorizeCategoryCreationTests {
    private func txn(_ id: String) -> Components.Schemas.Transaction {
        .init(id: id, accountId: "a", occurredAt: "2026-07-13",
              amount: .init(amountMinor: -1000, currency: "USD"), merchant: "Shop")
    }

    @Test func creatingACategoryAddsItSortedAndReturnsIt() async {
        let api = MockCategorizeAPI()
        api.cats = [.init(id: "c1", name: "Rent")]
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        let created = await vm.createCategory(named: "  Groceries ")

        #expect(created?.name == "Groceries")
        #expect(api.created == ["Groceries"])  // trimmed
        #expect(vm.categories.map(\.name) == ["Groceries", "Rent"])  // sorted
    }

    @Test func creatingAnExistingCategoryReusesItWithoutARoundTrip() async {
        let api = MockCategorizeAPI()
        api.cats = [.init(id: "c1", name: "Groceries")]
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        let created = await vm.createCategory(named: "groceries")  // case-insensitive

        #expect(created?.id == "c1")
        #expect(api.created.isEmpty)  // no server call
    }

    @Test func anEmptyNameCreatesNothing() async {
        let api = MockCategorizeAPI()
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        let created = await vm.createCategory(named: "   ")

        #expect(created == nil)
        #expect(api.created.isEmpty)
    }

    @Test func aConflictSurfacesAReadableError() async {
        let api = MockCategorizeAPI()
        api.createError = CategorizeError.categoryExists("Gas")
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        let created = await vm.createCategory(named: "Gas")

        #expect(created == nil)
        #expect(vm.errorMessage?.contains("already exists") == true)
    }
}

@MainActor
struct CategoryStarterSetTests {
    @Test func addsEveryStarterCategoryWhenNoneExist() async {
        let api = MockCategorizeAPI()
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        await vm.addStarterCategories()

        #expect(api.created.count == CategoryDefaults.starter.count)
        #expect(Set(vm.categories.map(\.name)) == Set(CategoryDefaults.starter))
    }

    @Test func skipsStartersThatAlreadyExistCaseInsensitively() async {
        let api = MockCategorizeAPI()
        api.cats = [.init(id: "c1", name: "groceries"), .init(id: "c2", name: "DINING")]
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        await vm.addStarterCategories()

        // Groceries and Dining already present -> not recreated.
        #expect(!api.created.contains("Groceries"))
        #expect(!api.created.contains("Dining"))
        #expect(api.created.count == CategoryDefaults.starter.count - 2)
    }

    /// A failure on one category leaves the rest created, and reports the error —
    /// the batch isn't all-or-nothing.
    @Test func oneFailureDoesNotAbortTheWholeBatch() async {
        final class FlakyAPI: CategorizeAPI, @unchecked Sendable {
            var madeCount = 0
            func uncategorized() async throws -> [Components.Schemas.Transaction] { [] }
            func categories() async throws -> [Components.Schemas.Category] { [] }
            func setCategory(transactionID: String, categoryID: String?) async throws {}
            func createCategory(name: String) async throws -> Components.Schemas.Category {
                madeCount += 1
                if name == "Transportation" { throw APIError.server(500) }
                return .init(id: name, name: name)
            }
        }
        let api = FlakyAPI()
        let vm = CategorizeViewModel(api: api)
        await vm.load()

        await vm.addStarterCategories()

        // Every name was attempted; all but the failing one landed.
        #expect(api.madeCount == CategoryDefaults.starter.count)
        #expect(vm.categories.count == CategoryDefaults.starter.count - 1)
        #expect(vm.errorMessage != nil)
    }

    @Test func starterSetHasNoDuplicates() {
        #expect(Set(CategoryDefaults.starter).count == CategoryDefaults.starter.count)
    }
}
