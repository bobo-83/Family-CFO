import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockReviewAPI: ReviewAPI, @unchecked Sendable {
    var items: [Components.Schemas.Transaction] = []
    var suspected: [Components.Schemas.Transaction] = []
    var existingCategories: [Components.Schemas.Category] = []
    private(set) var stateCalls: [(id: String, state: String)] = []
    private(set) var deleted: [String] = []
    private(set) var keptAsTransfer: [String] = []
    private(set) var createdCategories: [String] = []

    private(set) var categoryCalls: [(id: String, categoryID: String)] = []

    nonisolated func queue(kind: ReviewKind) async throws -> [Components.Schemas.Transaction] {
        await MainActor.run {
            switch kind {
            case .duplicates: return items
            case .suspectedIncome: return suspected
            default: return []
            }
        }
    }

    nonisolated func setCategory(transactionID: String, categoryID: String?) async throws {
        await MainActor.run {
            categoryCalls.append((transactionID, categoryID ?? ""))
            suspected.removeAll { $0.id == transactionID }
        }
    }

    nonisolated func keepAsTransfer(transactionID: String) async throws {
        await MainActor.run {
            keptAsTransfer.append(transactionID)
            suspected.removeAll { $0.id == transactionID }
        }
    }

    nonisolated func createCategory(name: String) async throws -> Components.Schemas.Category {
        await MainActor.run {
            createdCategories.append(name)
            let created = Components.Schemas.Category(id: "cat-\(name.lowercased())", name: name)
            existingCategories.append(created)
            return created
        }
    }

    nonisolated func categories() async throws -> [Components.Schemas.Category] {
        await MainActor.run { existingCategories }
    }

    nonisolated func setState(
        transactionID: String,
        state: Components.Schemas.TransactionUpdateRequest.DuplicateStatePayload
    ) async throws {
        await MainActor.run {
            stateCalls.append((transactionID, state.rawValue))
            items.removeAll {
                $0.id == transactionID && state == .dismissed
            }
            items = items.map { txn in
                guard txn.id == transactionID, state == .disputed else { return txn }
                var copy = txn
                copy.duplicateState = .disputed
                return copy
            }
        }
    }

    nonisolated func delete(transactionID: String) async throws {
        await MainActor.run {
            deleted.append(transactionID)
            items.removeAll { $0.id == transactionID }
        }
    }
}

@MainActor
struct ReviewViewModelTests {
    private func dup(
        _ id: String, amount: Int64 = -2_400, at: String = "2026-05-19",
        merchant: String = "Proclean", state: Components.Schemas.Transaction.DuplicateStatePayload = .flagged
    ) -> Components.Schemas.Transaction {
        .init(
            id: id,
            accountId: "acct-1",
            occurredAt: at,
            amount: .init(amountMinor: amount, currency: "USD"),
            merchant: merchant,
            duplicateState: state
        )
    }

    @Test func groupsIdenticalChargesAndCountsThem() async {
        let api = MockReviewAPI()
        api.items = [dup("a"), dup("b"), dup("c", amount: -999, merchant: "Cafe")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        // Two identical Proclean charges group together; the lone Cafe stands alone.
        #expect(vm.groups.count == 2)
        #expect(vm.reviewCount == 3)
        let proclean = vm.groups.first { $0.sample.merchant == "Proclean" }
        #expect(proclean?.count == 2)
    }

    @Test func keepAllDismissesEveryMemberOfTheGroup() async {
        let api = MockReviewAPI()
        api.items = [dup("a"), dup("b")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.keepAll(vm.groups[0])

        #expect(api.stateCalls.map(\.state) == ["dismissed", "dismissed"])
        #expect(vm.isEmpty)  // reloaded, nothing left
    }

    @Test func disputingMarksOneAndKeepsTheGroupVisible() async {
        let api = MockReviewAPI()
        api.items = [dup("a"), dup("b")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.dispute(vm.groups[0].transactions[0])

        #expect(vm.reviewCount == 2)  // both still present
        #expect(vm.groups[0].hasDisputed)
    }

    @Test func deletingRemovesTheCharge() async {
        let api = MockReviewAPI()
        api.items = [dup("a"), dup("b")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.delete(vm.groups[0].transactions[0])

        #expect(api.deleted == ["a"])
        #expect(vm.reviewCount == 1)
    }

    private func inflow(_ id: String, amount: Int64 = 300_000) -> Components.Schemas.Transaction {
        .init(
            id: id, accountId: "acct-1", occurredAt: "2026-05-26",
            amount: .init(amountMinor: amount, currency: "USD"),
            merchant: "Online Transfer", suspectedIncome: true)
    }

    @Test func loadsSuspectedIncomeSortedByAmount() async {
        let api = MockReviewAPI()
        api.suspected = [inflow("a", amount: 50_000), inflow("b", amount: 300_000)]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        #expect(vm.suspectedIncome.map(\.id) == ["b", "a"])
        #expect(!vm.nothingToReview)
    }

    @Test func confirmingAsIncomeCreatesIncomeCategoryAndRefiles() async {
        let api = MockReviewAPI()
        api.suspected = [inflow("a")]  // household has no Income category yet
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.confirmAsIncome(vm.suspectedIncome[0])

        #expect(api.createdCategories == ["Income"])
        #expect(api.categoryCalls.map(\.categoryID) == ["cat-income"])
        #expect(vm.suspectedIncome.isEmpty)  // reloaded, no longer suspected
    }

    @Test func confirmingReusesAnExistingIncomeCategory() async {
        let api = MockReviewAPI()
        api.existingCategories = [.init(id: "inc-1", name: "Income")]
        api.suspected = [inflow("a")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.confirmAsIncome(vm.suspectedIncome[0])

        #expect(api.createdCategories.isEmpty)
        #expect(api.categoryCalls.map(\.categoryID) == ["inc-1"])
    }

    @Test func keepingAsTransferExcludesItFromFlagging() async {
        let api = MockReviewAPI()
        api.suspected = [inflow("a")]
        let vm = ReviewViewModel(api: api)
        await vm.load()

        await vm.keepAsTransfer(vm.suspectedIncome[0])

        #expect(api.keptAsTransfer == ["a"])
        #expect(vm.suspectedIncome.isEmpty)
    }
}
