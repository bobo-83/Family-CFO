import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockReviewAPI: ReviewAPI, @unchecked Sendable {
    var items: [Components.Schemas.Transaction] = []
    private(set) var stateCalls: [(id: String, state: String)] = []
    private(set) var deleted: [String] = []

    private(set) var categoryCalls: [(id: String, categoryID: String)] = []

    nonisolated func queue(kind: ReviewKind) async throws -> [Components.Schemas.Transaction] {
        await MainActor.run { kind == .duplicates ? items : [] }
    }

    nonisolated func setCategory(transactionID: String, categoryID: String?) async throws {
        await MainActor.run { categoryCalls.append((transactionID, categoryID ?? "")) }
    }

    nonisolated func categories() async throws -> [Components.Schemas.Category] {
        await MainActor.run { [] }
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
}
