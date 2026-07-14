import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockHouseholdAPI: HouseholdAPI, @unchecked Sendable {
    var context: Components.Schemas.HouseholdContext?
    var error: Error?
    private(set) var callCount = 0

    var txns: [Components.Schemas.Transaction] = []

    nonisolated func context() async throws -> Components.Schemas.HouseholdContext {
        try await MainActor.run {
            callCount += 1
            if let error { throw error }
            return context!
        }
    }

    nonisolated func transactions() async throws -> [Components.Schemas.Transaction] {
        try await MainActor.run { txns }
    }
}

@MainActor
struct OverviewViewModelTests {
    private func money(_ minor: Int64) -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: "USD")
    }

    private func context() -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "The Vus",
            currency: "USD",
            netWorth: money(1_234_500),
            emergencyFundMonths: 4.5
        )
    }

    @Test func loadsTheHouseholdContext() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.context?.netWorth.amountMinor == 1_234_500)
        #expect(viewModel.errorMessage == nil)
        #expect(!viewModel.isLoading)
    }

    @Test func surfacesAFailureInsteadOfShowingStaleNumbers() async {
        let api = MockHouseholdAPI()
        api.error = APIError.unauthorized
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.context == nil)
        #expect(viewModel.errorMessage?.contains("pairing") == true)
    }

    /// Money is stored in minor units by contract (M2); rendering them raw
    /// would show a $12,345 net worth as "$1,234,500".
    @Test func moneyFormatsFromMinorUnits() {
        #expect(money(1_234_500).formatted == "$12,345")
        #expect(money(4_299).formattedExact == "$42.99")
    }

    @Test func dueDescriptionReadsNaturally() {
        #expect(OverviewView.dueDescription(daysUntil: -1) == "Overdue")
        #expect(OverviewView.dueDescription(daysUntil: 0) == "Due today")
        #expect(OverviewView.dueDescription(daysUntil: 1) == "Due tomorrow")
        #expect(OverviewView.dueDescription(daysUntil: 5) == "Due in 5 days")
    }
}

@MainActor
struct EmergencyFundPresentationTests {
    private func fund(
        months: Double?,
        recommended: Double,
        status: Components.Schemas.EmergencyFundSummary.StatusPayload
    ) -> Components.Schemas.EmergencyFundSummary {
        .init(
            months: months,
            reserved: .init(amountMinor: 500_000, currency: "USD"),
            usingDesignations: false,
            monthlyExpenses: .init(amountMinor: 100_000, currency: "USD"),
            targetMonthsMin: 3,
            targetMonthsRecommended: recommended,
            status: status
        )
    }

    @Test func progressIsAFractionOfTheHouseholdsOwnTarget() {
        let summary = fund(months: 3, recommended: 6, status: .gettingStarted)

        #expect(summary.progressToRecommended == 0.5)
    }

    @Test func overfundedProgressClampsToFull() {
        let summary = fund(months: 12, recommended: 6, status: .fullyFunded)

        #expect(summary.progressToRecommended == 1)
    }

    /// With no bills the server can't size the fund, so there is no honest
    /// denominator — the view must show no bar rather than invent one.
    @Test func noBillsMeansNoProgressBar() {
        let summary = fund(months: nil, recommended: 6, status: .noBills)

        #expect(summary.progressToRecommended == nil)
    }
}

struct SpokenReplySentenceTests {
    @Test func splitsIntoSentencesForChunkedPlayback() {
        let chunks = SpokenReply.sentences(
            "Your net worth is up. Bills look fine! Can we afford it?")

        #expect(
            chunks == [
                "Your net worth is up.", "Bills look fine!", "Can we afford it?",
            ])
    }

    @Test func aSingleSentenceIsOneChunk() {
        #expect(SpokenReply.sentences("Just the one.") == ["Just the one."])
    }

    @Test func emptyTextYieldsNothingToSay() {
        #expect(SpokenReply.sentences("   ").isEmpty)
    }
}

struct CategorySpendingDetailTests {
    private func txn(_ id: String, cat: String?, amount: Int64, at: String, merchant: String = "M")
        -> Components.Schemas.Transaction
    {
        .init(id: id, accountId: "a", occurredAt: at,
              amount: .init(amountMinor: amount, currency: "USD"),
              merchant: merchant, categoryId: cat)
    }

    private var sample: [Components.Schemas.Transaction] {
        [
            txn("a", cat: "dining", amount: -2000, at: "2026-07-12"),
            txn("b", cat: "dining", amount: -500, at: "2026-07-03"),
            txn("c", cat: "dining", amount: -9999, at: "2026-06-30"),  // last month
            txn("d", cat: "gas", amount: -1000, at: "2026-07-05"),     // other category
            txn("e", cat: "dining", amount: 4000, at: "2026-07-08"),   // income (positive)
            txn("f", cat: nil, amount: -700, at: "2026-07-09"),        // uncategorized
        ]
    }

    @Test func filtersToTheCategoryMonthAndOutflowsOnly() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "dining", month: "2026-07")

        // Only a and b: same category, July, outflow. c is last month; e is income.
        #expect(items.map(\.id) == ["a", "b"])  // biggest spend first
    }

    @Test func totalReconcilesWithTheCard() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "dining", month: "2026-07")
        let total = CategorySpendingDetail.total(items, currency: "USD")

        #expect(total.amountMinor == 2500)  // 2000 + 500, as positive spend
    }

    @Test func emptyWhenNothingMatches() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "travel", month: "2026-07")
        #expect(items.isEmpty)
    }
}
