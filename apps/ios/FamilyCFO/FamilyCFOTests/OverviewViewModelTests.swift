import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockHouseholdAPI: HouseholdAPI, @unchecked Sendable {
    var context: Components.Schemas.HouseholdContext?
    var error: Error?
    private(set) var callCount = 0

    var txns: [Components.Schemas.Transaction] = []
    var syncTotals = SyncTotals()
    private(set) var syncCallCount = 0

    nonisolated func context(month: String?) async throws
        -> Components.Schemas.HouseholdContext
    {
        try await MainActor.run {
            callCount += 1
            if let error { throw error }
            return context!
        }
    }

    nonisolated func transactions(month: String?) async throws
        -> [Components.Schemas.Transaction]
    {
        try await MainActor.run { txns }
    }

    nonisolated func syncAll() async throws -> SyncTotals {
        try await MainActor.run {
            syncCallCount += 1
            if let error { throw error }
            return syncTotals
        }
    }

    var outlook: Components.Schemas.CashOutlookResponse?
    nonisolated func cashOutlook() async throws -> Components.Schemas.CashOutlookResponse? {
        try await MainActor.run { outlook }
    }

    var plan: Components.Schemas.SpendingPlanResponse?
    nonisolated func spendingPlan() async throws -> Components.Schemas.SpendingPlanResponse? {
        try await MainActor.run { plan }
    }

    var monthlySpending: Components.Schemas.SpendingByCategory?
    nonisolated func spending(month: String?) async throws
        -> Components.Schemas.SpendingByCategory
    {
        try await MainActor.run {
            if let error { throw error }
            return monthlySpending
                ?? .init(
                    month: month ?? "2026-07", monthLabel: "July 2026",
                    categorizedTotal: .init(amountMinor: 0, currency: "USD"),
                    uncategorized: .init(amountMinor: 0, currency: "USD"))
        }
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

    /// M112: the cash outlook loads with the current month and its lowest point
    /// is the figure the card leads with.
    @Test func loadsTheCashOutlookForTheCurrentMonth() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.outlook = .init(
            startingCash: money(1_632_600),
            events: [
                .init(
                    occurredOn: "2026-08-14", name: "Platinum Card",
                    amount: money(-1_228_241), kind: .creditCard)
            ],
            endingCash: money(-485_100),
            lowestBalance: money(-485_100),
            lowestDate: "2026-08-14",
            expectedIncome: money(647_100),
            obligations: money(2_764_900),
            horizonDays: 30,
            dueSoon: money(825_400),
            dueSoonCovered: true,
            dueSoonWindowDays: 14)
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.outlook?.lowestBalance.amountMinor == -485_100)
        #expect(viewModel.outlook?.dueSoonCovered == true)
    }

    /// M113: the spending plan loads with the current month.
    @Test func loadsTheSpendingPlanForTheCurrentMonth() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.plan = .init(
            month: "2026-07",
            incomeReceived: money(401_000),
            incomeProjected: money(324_000),
            expectedIncome: money(725_100),
            spent: money(1_284_000),
            billsRemaining: money(3_800),
            accountObligations: money(438_400),
            plannedSavings: money(0),
            leftToSpend: money(-1_001_100),
            perDay: money(0),
            daysRemaining: 15)
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.plan?.leftToSpend.amountMinor == -1_001_100)
        #expect(viewModel.plan?.daysRemaining == 15)
    }

    @Test func syncNowFetchesThenReloadsAndReports() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.syncTotals = SyncTotals(imported: 4, transfersFiled: 1, autoCategorized: 2)
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.syncNow()

        #expect(api.syncCallCount == 1)
        #expect(api.callCount == 1)  // reloaded context after syncing
        #expect(viewModel.syncResult?.contains("4") == true)
        #expect(!viewModel.isSyncing)
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
            txn("e", cat: "dining", amount: 500, at: "2026-07-08"),    // refund in dining
            txn("f", cat: nil, amount: -700, at: "2026-07-09"),        // uncategorized
        ]
    }

    @Test func includesTheCategoryMonthAndItsRefunds() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "dining", month: "2026-07")

        // a, b (purchases) and e (a refund in dining); c is last month, d other category.
        #expect(items.map(\.id) == ["a", "b", "e"])  // biggest spend first, refund last
    }

    @Test func totalNetsRefundsAgainstSpend() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "dining", month: "2026-07")
        let total = CategorySpendingDetail.total(items, currency: "USD")

        #expect(total.amountMinor == 2000)  // 2000 + 500 spend − 500 refund
    }

    @Test func emptyWhenNothingMatches() {
        let items = CategorySpendingDetail.items(in: sample, categoryID: "travel", month: "2026-07")
        #expect(items.isEmpty)
    }

    @Test func pairsARefundWithItsPurchase() {
        let items = [
            txn("buy", cat: "shopping", amount: -7500, at: "2026-07-02", merchant: "Lululemon"),
            txn("ref", cat: "shopping", amount: 7500, at: "2026-07-09", merchant: "Lululemon Athletica"),
            txn("other", cat: "shopping", amount: -2000, at: "2026-07-05", merchant: "Target"),
        ]
        let rows = CategorySpendingDetail.grouped(items)

        #expect(rows.count == 2)  // the pair collapses into one row + Target
        guard case .refunded(let purchase, let refund) = rows[0] else {
            Issue.record("expected a refunded pair first")
            return
        }
        #expect(purchase.id == "buy")
        #expect(refund.id == "ref")
        guard case .single(let single) = rows[1] else {
            Issue.record("expected Target as a single row")
            return
        }
        #expect(single.id == "other")
    }

    @Test func leavesAnUnmatchedRefundOnItsOwn() {
        let items = [txn("credit", cat: "shopping", amount: 5000, at: "2026-07-02", merchant: "Amex")]
        let rows = CategorySpendingDetail.grouped(items)
        #expect(rows.count == 1)
        guard case .single = rows[0] else {
            Issue.record("a lone refund should be a single row")
            return
        }
    }
}
