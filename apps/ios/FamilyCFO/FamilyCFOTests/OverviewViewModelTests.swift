import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

func testQualified(
    _ minor: Int64,
    currency: String = "USD",
    incompleteCount: Int = 0
) -> Components.Schemas.QualifiedMoney {
    .init(
        value: .init(amountMinor: minor, currency: currency),
        incompleteCount: incompleteCount)
}

func testAvailability(
    _ status: Components.Schemas.ComputationAvailability.StatusPayload = .complete,
    incompleteCount: Int = 0
) -> Components.Schemas.ComputationAvailability {
    .init(status: status, incompleteCount: incompleteCount)
}

func testSavingsSet(
    _ contributions: [Components.Schemas.SavingsContribution] = [],
    detection: Components.Schemas.ComputationAvailability = testAvailability()
) -> Components.Schemas.SavingsContributionSet {
    .init(contributions: contributions, detection: detection)
}

@MainActor
final class MockHouseholdAPI: HouseholdAPI, @unchecked Sendable {
    var context: Components.Schemas.HouseholdContext?
    var error: Error?
    private(set) var callCount = 0

    var txns: [Components.Schemas.Transaction] = []
    var syncTotals = SyncTotals()
    private(set) var syncCallCount = 0

    /// What GET /health reports, for the contract comparison (ADR 0074). nil —
    /// the protocol's own default — is an unreachable box.
    var version: String?

    nonisolated func serverVersion() async -> String? {
        await MainActor.run { version }
    }

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
        await MainActor.run { txns }
    }

    nonisolated func syncAll() async throws -> SyncTotals {
        try await MainActor.run {
            syncCallCount += 1
            if let error { throw error }
            return syncTotals
        }
    }

    var outlook: Components.Schemas.CashOutlookResponse?
    var outlookError: Error?
    nonisolated func cashOutlook() async throws -> Components.Schemas.CashOutlookResponse? {
        try await MainActor.run {
            if let outlookError { throw outlookError }
            return outlook
        }
    }

    var plan: Components.Schemas.SpendingPlanResponse?
    var planError: Error?
    nonisolated func spendingPlan() async throws -> Components.Schemas.SpendingPlanResponse? {
        try await MainActor.run {
            if let planError { throw planError }
            return plan
        }
    }

    /// #203 mutations. Kept separate from `error` so a test can fail the write
    /// while the refresh that follows it would have succeeded.
    var mutationError: Error?
    private(set) var declared: [Components.Schemas.SavingsContributionCreateRequest] = []
    private(set) var deletedContributionIDs: [String] = []
    private(set) var dismissedRoutes: [(source: String, destination: String)] = []

    nonisolated func declareSavingsContribution(
        _ request: Components.Schemas.SavingsContributionCreateRequest
    ) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            declared.append(request)
        }
    }

    nonisolated func deleteSavingsContribution(id: String) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            deletedContributionIDs.append(id)
        }
    }

    nonisolated func dismissSavingsContribution(
        sourceAccountID: String, destinationAccountID: String
    ) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            dismissedRoutes.append((source: sourceAccountID, destination: destinationAccountID))
        }
    }

    // #4: goal links. A nil goalID is the unlink.
    private(set) var links: [(id: String, goalID: String?)] = []
    nonisolated func updateSavingsContribution(id: String, goalID: String?) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            links.append((id: id, goalID: goalID))
        }
    }

    // #10: household language PATCHes.
    private(set) var updatedLanguages: [String] = []
    nonisolated func updateLanguage(_ language: String) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            updatedLanguages.append(language)
        }
    }

    // #41: household time-zone PATCHes. #43: nil is a clear, not "no change".
    private(set) var updatedTimezones: [String?] = []
    nonisolated func updateTimezone(_ identifier: String?) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            updatedTimezones.append(identifier)
        }
    }

    // #5: reserve-committed-savings PATCHes.
    private(set) var reserveUpdates: [Bool] = []
    nonisolated func updateReserveCommittedSavings(_ value: Bool) async throws {
        try await MainActor.run {
            if let mutationError { throw mutationError }
            reserveUpdates.append(value)
        }
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
                    categorizedTotal: testQualified(0),
                    uncategorized: testQualified(0),
                    total: testQualified(0))
        }
    }
}

/// #4: the Overview only ever asks the Goals API for the id→name map, and only
/// lazily — the call count is the test's whole point.
@MainActor
final class MockGoalsAPI: GoalsAPI, @unchecked Sendable {
    var result: [Components.Schemas.Goal] = []
    private(set) var callCount = 0

    nonisolated func goals() async throws -> [Components.Schemas.Goal] {
        await MainActor.run {
            callCount += 1
            return result
        }
    }
    /// #156: what was sent, so a test can pin the currency of a new goal and of an edit.
    private(set) var created: [Components.Schemas.GoalCreateRequest] = []
    private(set) var updated: [(id: String, request: Components.Schemas.GoalUpdateRequest)] = []
    nonisolated func createGoal(_ request: Components.Schemas.GoalCreateRequest) async throws {
        await MainActor.run { created.append(request) }
    }
    nonisolated func updateGoal(
        id: String, _ request: Components.Schemas.GoalUpdateRequest
    ) async throws {
        await MainActor.run { updated.append((id, request)) }
    }
    nonisolated func deleteGoal(id: String) async throws {}
}


/// #156 (ADR 0075): the note under the net-worth value names what the
/// base-currency total left out, in each account's own currency — and nothing
/// for an empty list (known, none) or a nil one (a past month, unknown).
@MainActor
struct OutsideBaseCurrencyNoteTests {
    private func context(
        _ outside: [Components.Schemas.AccountOutsideBaseCurrency]?
    ) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1", displayName: "demo", currency: "USD",
            netWorth: testQualified(-298_000_000),
            emergencyFundMonths: 0, savingsContributions: testSavingsSet(),
            accountsOutsideBaseCurrency: outside)
    }

    @Test func namesEachExcludedAccountInItsOwnCurrency() {
        let note = OverviewViewModel.outsideBaseCurrencyNote(
            context([
                .init(name: "Euro Savings", balance: .init(amountMinor: 400_000, currency: "EUR")),
                .init(name: "Euro Pension", balance: .init(amountMinor: 900_000, currency: "EUR")),
            ]))

        let text = try! #require(note)
        #expect(text.hasPrefix("Not counted in USD totals: "))
        #expect(text.contains("Euro Savings"))
        #expect(text.contains("Euro Pension"))
        #expect(text.contains(" · "))
        // EUR 4,000.00 formatted as euros, never as dollars.
        #expect(text.contains("€4,000.00") || text.contains("4,000.00 €") || text.contains("EUR 4,000.00"))
        #expect(!text.contains("$4,000.00"))
    }

    @Test func nothingForAnEmptyList() {
        #expect(OverviewViewModel.outsideBaseCurrencyNote(context([])) == nil)
    }

    @Test func nothingForAPastMonthWhereTheListIsNil() {
        #expect(OverviewViewModel.outsideBaseCurrencyNote(context(nil)) == nil)
    }
}

actor ControlledOverviewAPI: HouseholdAPI {
    private var pendingContexts: [CheckedContinuation<Components.Schemas.HouseholdContext, Error>?] = []
    private var pendingSync: CheckedContinuation<SyncTotals, Error>?
    private(set) var requestedMonths: [String?] = []

    func context(month: String?) async throws -> Components.Schemas.HouseholdContext {
        requestedMonths.append(month)
        return try await withCheckedThrowingContinuation { continuation in
            pendingContexts.append(continuation)
        }
    }

    func waitForContextRequests(_ count: Int) async {
        while pendingContexts.count < count { await Task.yield() }
    }

    func resolveContext(
        at index: Int, with value: Components.Schemas.HouseholdContext
    ) {
        guard pendingContexts.indices.contains(index), let continuation = pendingContexts[index]
        else { return }
        pendingContexts[index] = nil
        continuation.resume(returning: value)
    }

    func rejectContext(at index: Int) {
        guard pendingContexts.indices.contains(index), let continuation = pendingContexts[index]
        else { return }
        pendingContexts[index] = nil
        continuation.resume(throwing: APIError.server(503))
    }

    func transactions(month: String?) async throws -> [Components.Schemas.Transaction] { [] }
    func syncAll() async throws -> SyncTotals {
        try await withCheckedThrowingContinuation { pendingSync = $0 }
    }

    func waitForSyncRequest() async {
        while pendingSync == nil { await Task.yield() }
    }

    func resolveSync(_ totals: SyncTotals = SyncTotals()) {
        let continuation = pendingSync
        pendingSync = nil
        continuation?.resume(returning: totals)
    }
    func spending(month: String?) async throws -> Components.Schemas.SpendingByCategory {
        .init(
            month: month ?? MonthKey.current(), monthLabel: "Current month",
            categorizedTotal: testQualified(0), uncategorized: testQualified(0),
            total: testQualified(0))
    }
}

struct StaticBudgetsAPI: BudgetsAPI {
    let response: Components.Schemas.BudgetListResponse

    func budgets() async throws -> Components.Schemas.BudgetListResponse { response }
    func categories() async throws -> [Components.Schemas.Category] { [] }
    func createBudget(categoryID: String, limitMinor: Int64, currency: String) async throws {}
    func updateBudget(id: String, limitMinor: Int64, currency: String) async throws {}
    func deleteBudget(id: String) async throws {}
}

@MainActor
struct BudgetSummaryOwnershipTests {
    @Test func viewModelPreservesServerSummaryWithoutReducingBudgetRows() async {
        let row = Components.Schemas.Budget(
            id: "budget-1", categoryId: "category-1", categoryName: "Food",
            limit: .init(amountMinor: 10_000, currency: "USD"),
            spent: testQualified(2_000, incompleteCount: 1))
        let serverSummary = Components.Schemas.BudgetSummary(
            envelopeCount: 1, overCount: nil, warningCount: nil,
            totalBudgeted: .init(amountMinor: 10_000, currency: "USD"),
            totalSpent: testQualified(9_000, incompleteCount: 3))
        let viewModel = BudgetsViewModel(
            api: StaticBudgetsAPI(response: .init(budgets: [row], summary: serverSummary)))

        await viewModel.load()

        #expect(viewModel.budgets.first?.spent.amountMinor == 2_000)
        #expect(viewModel.summary?.totalSpent.amountMinor == 9_000)
        #expect(viewModel.summary?.totalSpent.incompleteCount == 3)
        #expect(viewModel.summary?.overCount == nil)
        #expect(BudgetsView.statusLine(row) == unavailableValueText)
    }
}

@MainActor
struct SpendingServerTotalTests {
    @Test func partialZeroIsNotAnEmptySpendingMonth() {
        let spending = Components.Schemas.SpendingByCategory(
            month: "2026-08", monthLabel: "August 2026", categories: nil,
            categorizedTotal: testQualified(0), uncategorized: testQualified(0),
            total: testQualified(0, incompleteCount: 1))

        #expect(!SpendingCard.isEmpty(spending))
    }

    @Test func emptyStateUsesServerTotalRatherThanAddingChildren() {
        let spending = Components.Schemas.SpendingByCategory(
            month: "2026-08", monthLabel: "August 2026", categories: [],
            categorizedTotal: testQualified(0), uncategorized: testQualified(0),
            total: testQualified(5_000))

        #expect(!SpendingCard.isEmpty(spending))
    }
}

@MainActor
struct OverviewViewModelTests {
    private func money(_ minor: Int64) -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: "USD")
    }

    private func context(
        contributions: [Components.Schemas.SavingsContribution]? = nil
    ) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "demo-household",
            currency: "USD",
            netWorth: testQualified(1_234_500),
            emergencyFundMonths: 4.5,
            savingsContributions: testSavingsSet(contributions ?? [])
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
            endingCash: testQualified(-485_100),
            lowestBalance: testQualified(-485_100),
            lowestDate: "2026-08-14",
            expectedIncome: testQualified(647_100),
            incomeProjection: testAvailability(),
            obligations: testQualified(2_764_900),
            horizonDays: 30,
            dueSoon: testQualified(825_400),
            dueSoonCovered: true,
            dueSoonWindowDays: 14)
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.outlook?.lowestBalance?.amountMinor == -485_100)
        #expect(viewModel.outlook?.dueSoonCovered == true)
    }

    /// M113: the spending plan loads with the current month.
    @Test func loadsTheSpendingPlanForTheCurrentMonth() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.plan = .init(
            month: "2026-07",
            incomeReceived: testQualified(401_000),
            incomeProjected: testQualified(324_000),
            expectedIncome: testQualified(725_100),
            incomeProjection: testAvailability(),
            spent: testQualified(1_284_000),
            billsRemaining: testQualified(3_800),
            accountObligations: money(438_400),
            plannedSavings: money(0),
            leftToSpend: testQualified(-1_001_100),
            perDay: testQualified(0),
            daysRemaining: 15)
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.plan?.leftToSpend?.amountMinor == -1_001_100)
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

    @Test func changingMonthDuringSyncStillClearsSyncOwnership() async {
        let api = ControlledOverviewAPI()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        let sync = Task { await viewModel.syncNow() }
        await api.waitForSyncRequest()
        let monthLoad = Task { await viewModel.show(month: "2026-01") }
        await api.waitForContextRequests(1)

        await api.resolveSync()
        _ = await sync.value
        #expect(!viewModel.isSyncing)

        await api.resolveContext(at: 0, with: context())
        _ = await monthLoad.value
    }

    @Test func surfacesAFailureInsteadOfShowingStaleNumbers() async {
        let api = MockHouseholdAPI()
        api.error = APIError.unauthorized
        let viewModel = OverviewViewModel(api: api, notifications: nil)

        await viewModel.load()

        #expect(viewModel.context == nil)
        #expect(viewModel.errorMessage?.contains("pairing") == true)
    }

    @Test func optionalFailureKeepsAuthoritativeContext() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.outlookError = APIError.server(503)
        api.planError = APIError.server(503)
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: nil)

        await viewModel.load()

        #expect(viewModel.context?.householdId == "hh-1")
        #expect(viewModel.errorMessage == nil)
        #expect(viewModel.outlook == nil)
        #expect(viewModel.plan == nil)
        #expect(viewModel.outlookErrorMessage != nil)
        #expect(viewModel.planErrorMessage != nil)
    }

    @Test func ownerValidCurrentFailureClearsDecisionsGoalNamesAndSnapshot() async {
        let api = MockHouseholdAPI()
        let goals = MockGoalsAPI()
        goals.result = [
            .init(
                id: "goal-1", name: "College", _type: .college,
                target: money(1_000_000), current: money(100_000), priority: 1)
        ]
        api.context = context(contributions: [
            contribution(
                "College", amount: 10_000, frequency: .monthly,
                monthly: 10_000, occurrences: 2, goalID: "goal-1")
        ])
        api.outlook = .init(
            startingCash: money(500_000), events: [], endingCash: testQualified(450_000),
            lowestBalance: testQualified(400_000), lowestDate: "2026-09-10",
            expectedIncome: testQualified(100_000), incomeProjection: testAvailability(),
            obligations: testQualified(50_000), horizonDays: 30,
            dueSoon: testQualified(25_000), dueSoonCovered: true, dueSoonWindowDays: 14)
        api.plan = .init(
            month: MonthKey.current(), incomeReceived: testQualified(100_000),
            incomeProjected: testQualified(50_000), expectedIncome: testQualified(150_000),
            incomeProjection: testAvailability(), spent: testQualified(40_000),
            billsRemaining: testQualified(20_000), accountObligations: money(10_000),
            plannedSavings: money(5_000), leftToSpend: testQualified(75_000),
            perDay: testQualified(3_000), daysRemaining: 25)
        let suite = "test.overview.failure.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        let viewModel = OverviewViewModel(
            api: api, goalsAPI: goals, notifications: nil, snapshotStore: store)

        await viewModel.load()
        #expect(viewModel.context != nil)
        #expect(viewModel.outlook != nil)
        #expect(viewModel.plan != nil)
        #expect(viewModel.goalNames["goal-1"] == "College")
        #expect(store.load() != nil)

        api.error = APIError.server(503)
        await viewModel.load()

        #expect(viewModel.context == nil)
        #expect(viewModel.outlook == nil)
        #expect(viewModel.plan == nil)
        #expect(viewModel.goalNames.isEmpty)
        #expect(viewModel.outlookErrorMessage == nil)
        #expect(viewModel.planErrorMessage == nil)
        #expect(viewModel.errorMessage != nil)
        #expect(store.load() == nil)
    }

    @Test func historicalContextFailurePreservesCurrentSnapshot() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let suite = "test.overview.historical-failure.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: store)
        await viewModel.load()
        let currentSnapshot = try! #require(store.load())

        api.error = APIError.server(503)
        await viewModel.show(month: "2026-08")

        #expect(viewModel.context == nil)
        #expect(viewModel.errorMessage != nil)
        #expect(store.load() == currentSnapshot)
    }

    @Test func staleContextFailureCannotClearNewerSuccessOrSnapshot() async {
        let api = ControlledOverviewAPI()
        let suite = "test.overview.stale-failure.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: store,
            ownerIdentity: { "household-a:session-1" })

        let older = Task { await viewModel.load() }
        await api.waitForContextRequests(1)
        let newer = Task { await viewModel.load() }
        await api.waitForContextRequests(2)
        await api.resolveContext(at: 1, with: context(netWorthMinor: 222))
        _ = await newer.value
        await api.rejectContext(at: 0)
        _ = await older.value

        #expect(viewModel.context?.netWorth.amountMinor == 222)
        #expect(viewModel.errorMessage == nil)
        #expect(store.load()?.netWorthMinor == 222)
    }

    @Test func newerSameMonthLoadWinsAndOwnsTheSnapshot() async {
        let api = ControlledOverviewAPI()
        let suite = "test.overview.owner.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        let identity = "household-a:session-1"
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: store,
            ownerIdentity: { identity })

        let older = Task { await viewModel.load() }
        await api.waitForContextRequests(1)
        let newer = Task { await viewModel.load() }
        await api.waitForContextRequests(2)

        await api.resolveContext(at: 1, with: context(netWorthMinor: 222))
        _ = await newer.value
        await api.resolveContext(at: 0, with: context(netWorthMinor: 111))
        _ = await older.value

        #expect(viewModel.context?.netWorth.amountMinor == 222)
        #expect(store.load()?.netWorthMinor == 222)
    }

    @Test func cancelledLoadCannotCommit() async {
        let api = ControlledOverviewAPI()
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: nil,
            ownerIdentity: { "household-a:session-1" })
        let load = Task { await viewModel.load() }
        await api.waitForContextRequests(1)

        load.cancel()
        await api.resolveContext(at: 0, with: context(netWorthMinor: 111))
        _ = await load.value

        #expect(viewModel.context == nil)
    }

    @Test func sessionReplacementRejectsCompletionAndSnapshotSideEffect() async {
        let api = ControlledOverviewAPI()
        let suite = "test.overview.session.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        store.save(
            OverviewSnapshot(
                netWorthMinor: 999, currency: "USD", emergencyFundStatus: "On track",
                emergencyFundMonths: 4, capturedAt: Date()))
        var identity = "household-a:session-1"
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: store,
            ownerIdentity: { identity })
        let load = Task { await viewModel.load() }
        await api.waitForContextRequests(1)

        identity = "household-b:session-2"
        await api.resolveContext(at: 0, with: context(netWorthMinor: 111))
        _ = await load.value

        #expect(viewModel.context == nil)
        #expect(store.load()?.netWorthMinor == 999)
    }

    @Test func monthReplacementRejectsOldCurrentSideEffects() async {
        let api = ControlledOverviewAPI()
        let suite = "test.overview.month.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = OverviewSnapshotStore(suiteName: suite)
        store.save(
            OverviewSnapshot(
                netWorthMinor: 999, currency: "USD", emergencyFundStatus: "On track",
                emergencyFundMonths: 4, capturedAt: Date()))
        let viewModel = OverviewViewModel(
            api: api, notifications: nil, snapshotStore: store,
            ownerIdentity: { "household-a:session-1" })

        let current = Task { await viewModel.load() }
        await api.waitForContextRequests(1)
        let historical = Task { await viewModel.show(month: "2026-08") }
        await api.waitForContextRequests(2)
        await api.resolveContext(at: 1, with: context(netWorthMinor: 222))
        _ = await historical.value
        await api.resolveContext(at: 0, with: context(netWorthMinor: 111))
        _ = await current.value

        #expect(viewModel.context?.netWorth.amountMinor == 222)
        #expect(store.load()?.netWorthMinor == 999)
    }

    private func context(netWorthMinor: Int64) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1", displayName: "demo-household", currency: "USD",
            netWorth: testQualified(netWorthMinor), emergencyFundMonths: 4.5,
            savingsContributions: testSavingsSet())
    }

    /// Money is stored in minor units by contract (M2); rendering them raw
    /// would show a $12,345 net worth as "$1,234,500".
    @Test func moneyFormatsFromMinorUnits() {
        #expect(money(1_234_500).formatted == "$12,345")
        #expect(money(4_299).formattedExact == "$42.99")
    }

    @Test func qualifiedMoneyDisclosesPartialZeroAndPluralizesForVoiceOver() {
        let exact = testQualified(0)
        let singular = testQualified(0, incompleteCount: 1)
        let plural = testQualified(4_299, incompleteCount: 2)

        #expect(!exact.isPartial)
        #expect(exact.partialDisclosure == nil)
        #expect(singular.isPartial)
        #expect(singular.partialDisclosure?.contains("1 stored amount") == true)
        #expect(plural.partialDisclosure?.contains("2 stored amounts") == true)
        #expect(singular.accessibilityDescription.contains(singular.formatted))
        #expect(singular.accessibilityDescription.contains("Partial total"))
    }

    @Test func unavailableAvailabilityIsDistinctFromExactZero() {
        let unavailable = testAvailability(.unavailable, incompleteCount: 2)
        let complete = testAvailability()

        #expect(unavailable.isUnavailable)
        #expect(unavailable.unavailableDisclosure?.contains("2 stored amounts") == true)
        #expect(!complete.isUnavailable)
        #expect(complete.unavailableDisclosure == nil)
    }

    @Test func dueDescriptionReadsNaturally() {
        #expect(OverviewView.dueDescription(daysUntil: -1) == "Overdue")
        #expect(OverviewView.dueDescription(daysUntil: 0) == "Due today")
        #expect(OverviewView.dueDescription(daysUntil: 1) == "Due tomorrow")
        #expect(OverviewView.dueDescription(daysUntil: 5) == "Due in 5 days")
    }

    // MARK: - #201 detected savings contributions

    private func contribution(
        _ name: String,
        amount: Int64,
        frequency: Components.Schemas.RecurringFrequency,
        monthly: Int64,
        occurrences: Int,
        inferred: Bool = false,
        declared: Bool = false,
        contributionID: String? = nil,
        sourceAccountID: String? = nil,
        destinationAccountID: String? = nil,
        goalID: String? = nil,
        suggestedGoalID: String? = nil
    ) -> Components.Schemas.SavingsContribution {
        .init(
            destinationName: name,
            destinationType: "529",
            amount: money(amount),
            frequency: frequency,
            monthlyEquivalent: money(monthly),
            occurrences: occurrences,
            lastSeen: "2026-07-01",
            inferred: inferred,
            declared: declared,
            contributionId: contributionID,
            sourceAccountId: sourceAccountID,
            destinationAccountId: destinationAccountID,
            goalId: goalID,
            suggestedGoalId: suggestedGoalID)
    }

    /// The only computation on this card. Cadences differ, so summing the raw
    /// amounts would count a $1,200 annual transfer as $1,200 a month — the
    /// server's monthly_equivalent is the only summable figure.
    @Test func monthlyTotalSumsTheNormalisedCadences() {
        let contributions = [
            contribution("College 529", amount: 50_000, frequency: .monthly, monthly: 50_000, occurrences: 4),
            contribution("Brokerage", amount: 120_000, frequency: .quarterly, monthly: 40_000, occurrences: 3),
            contribution("Rainy Day Savings", amount: 120_000, frequency: .annual, monthly: 10_000, occurrences: 1),
        ]

        let total = OverviewView.monthlyTotal(contributions)

        #expect(total?.amountMinor == 100_000)
        #expect(total?.currency == "USD")
    }

    /// Nothing detected means no card at all — never a $0 total.
    @Test func noContributionsMeansNoTotal() {
        #expect(OverviewView.monthlyTotal([]) == nil)
    }

    @Test func unavailableDetectionDoesNotTurnReadableRowsIntoAnExactTotal() {
        let set = Components.Schemas.SavingsContributionSet(
            contributions: [
                contribution(
                    "Declared 529", amount: 50_000, frequency: .monthly,
                    monthly: 50_000, occurrences: 0, declared: true)
            ],
            detection: .init(status: .unavailable, incompleteCount: 1))

        #expect(OverviewView.monthlyTotal(set) == nil)
        #expect(set.contributions.count == 1)
    }

    @Test func contributionRowsReadInPlainEnglish() {
        #expect(
            OverviewView.contributionDetail(
                contribution("College 529", amount: 50_000, frequency: .monthly, monthly: 50_000, occurrences: 4))
                == "$500.00 monthly · seen 4 times")
        #expect(
            OverviewView.contributionDetail(
                contribution("Rainy Day Savings", amount: 120_000, frequency: .annual, monthly: 10_000, occurrences: 1))
                == "$1,200.00 yearly · seen 1 time")
        #expect(OverviewView.cadenceWord(.biweekly) == "every two weeks")
        #expect(OverviewView.cadenceWord(.semimonthly) == "twice a month")
        #expect(OverviewView.cadenceWord(.semiannual) == "twice a year")
    }

    /// #207: the inferred caveat only appears when a row is actually inferred.
    @Test func inferredFootnoteAppearsOnlyWhenARowIsInferred() {
        let inferredCaveat =
            "Rows marked inferred were matched from the money leaving your account — "
            + "the destination isn't synced."
        let payroll =
            "Detected from transfers between your accounts. "
            + "Payroll deductions like a 401(k) don't appear here."

        let mixed = [
            contribution(
                "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
                occurrences: 4, inferred: true),
            contribution(
                "Rainy Day Savings", amount: 20_000, frequency: .monthly, monthly: 20_000,
                occurrences: 6),
        ]
        #expect(OverviewView.savingsFootnote(mixed) == "\(payroll) \(inferredCaveat)")

        let seenBothLegs = [
            contribution(
                "Rainy Day Savings", amount: 20_000, frequency: .monthly, monthly: 20_000,
                occurrences: 6)
        ]
        #expect(OverviewView.savingsFootnote(seenBothLegs) == payroll)
    }

    // MARK: - #6 observed savings rate

    private func savingsRate(
        transfers: Int64? = nil,
        payroll: Int64? = nil,
        residual: Int64? = nil,
        payrollProfilePresent: Bool? = nil
    ) -> Components.Schemas.SavingsRate {
        .init(
            percent: 12,
            monthlyIncome: testQualified(800_000),
            averageMonthlySpending: testQualified(500_000),
            transfers: transfers.map { testQualified($0) },
            transferDetection: testAvailability(),
            payrollDeductions: payroll.map { .init(amountMinor: $0, currency: "USD") },
            residual: residual.map { testQualified($0) },
            payrollProfilePresent: payrollProfilePresent
        )
    }

    /// #6: the breakdown names all three observed sources, in order.
    @Test func savingsBreakdownShowsTheThreeSources() {
        let breakdown = OverviewView.savingsBreakdown(
            savingsRate(transfers: 50_000, payroll: 250_000, residual: 30_000))
        #expect(breakdown == "$500 transfers · $2,500 payroll · $300 residual")
    }

    /// A source the box didn't send is simply left out; nil when none are.
    @Test func savingsBreakdownOmitsAbsentSources() {
        #expect(
            OverviewView.savingsBreakdown(savingsRate(transfers: 50_000))
                == "$500 transfers")
        #expect(OverviewView.savingsBreakdown(savingsRate()) == nil)
    }

    /// #6: the understatement note appears only when the box reports no payroll
    /// profile on file — not when it's present, and not when it's unknown.
    @Test func payrollNoteTogglesOnPayrollProfilePresent() {
        #expect(OverviewView.payrollNote(savingsRate(payrollProfilePresent: false)) != nil)
        #expect(OverviewView.payrollNote(savingsRate(payrollProfilePresent: true)) == nil)
        #expect(OverviewView.payrollNote(savingsRate(payrollProfilePresent: nil)) == nil)
    }

    // MARK: - #203 declared savings contributions

    private func declaration() -> Components.Schemas.SavingsContributionCreateRequest {
        .init(
            sourceAccountId: "acct-checking",
            destinationAccountId: "acct-529",
            amount: money(50_000),
            frequency: .monthly)
    }

    /// The whole point of #203: the household states a contribution the ledger
    /// can't show, and the Overview immediately reflects it.
    @Test func declaringPostsTheContributionThenRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        let posted = await viewModel.declareContribution(declaration())

        #expect(posted)
        #expect(api.declared.count == 1)
        #expect(api.declared.first?.destinationAccountId == "acct-529")
        #expect(api.declared.first?.amount.amountMinor == 50_000)
        #expect(api.callCount == 1)  // reloaded the Overview after declaring
        #expect(viewModel.errorMessage == nil)
    }

    @Test func stopTrackingDeletesTheDeclarationThenRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.stopTracking(contributionID: "sc-1")

        #expect(api.deletedContributionIDs == ["sc-1"])
        #expect(api.callCount == 1)
        #expect(viewModel.errorMessage == nil)
    }

    @Test func dismissingADetectedRouteSuppressesItThenRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.dismissRoute(
            sourceAccountID: "acct-checking", destinationAccountID: "acct-brokerage")

        #expect(api.dismissedRoutes.count == 1)
        #expect(api.dismissedRoutes.first?.source == "acct-checking")
        #expect(api.dismissedRoutes.first?.destination == "acct-brokerage")
        #expect(api.callCount == 1)
    }

    /// A refused write must say so and must NOT refresh — a silent reload would
    /// look exactly like a success that didn't stick.
    @Test func aRefusedDeclarationSurfacesTheErrorAndDoesNotRefresh() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.mutationError = APIError.server(423)
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        let posted = await viewModel.declareContribution(declaration())

        #expect(!posted)
        #expect(api.declared.isEmpty)
        #expect(api.callCount == 0)
        #expect(viewModel.errorMessage?.contains("sealed") == true)
    }

    @Test func aRefusedDeleteSurfacesTheError() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.mutationError = APIError.unauthorized
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.stopTracking(contributionID: "sc-1")

        #expect(api.callCount == 0)
        #expect(viewModel.errorMessage?.contains("pairing") == true)
    }

    /// Declared rows were never seen in the ledger, so an occurrence count would
    /// read "seen 0 times".
    @Test func declaredRowsSayWhoseWordTheyAre() {
        let declared = contribution(
            "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
            occurrences: 0, declared: true, contributionID: "sc-1")

        #expect(
            OverviewView.contributionDetail(declared)
                == "$500.00 monthly · declared by your family")
        #expect(
            OverviewView.savingsFootnote([declared]).hasSuffix(
                "Rows marked declared are your family's own word, counted whether or not "
                    + "either account syncs."))
    }

    /// Dismissal suppresses a ROUTE, so it needs both of its accounts. A row
    /// matched from one leg only names no route, and offers no action.
    @Test func onlyDetectedRowsWithBothAccountsCanBeDismissed() {
        let bothLegs = contribution(
            "Rainy Day Savings", amount: 20_000, frequency: .monthly, monthly: 20_000,
            occurrences: 6, sourceAccountID: "acct-checking",
            destinationAccountID: "acct-savings")
        #expect(OverviewView.dismissableRoute(bothLegs)?.source == "acct-checking")
        #expect(OverviewView.dismissableRoute(bothLegs)?.destination == "acct-savings")

        let arrivalOnly = contribution(
            "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
            occurrences: 4, sourceAccountID: "", destinationAccountID: "acct-529")
        #expect(OverviewView.dismissableRoute(arrivalOnly) == nil)

        let olderServer = contribution(
            "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
            occurrences: 4)
        #expect(OverviewView.dismissableRoute(olderServer) == nil)

        // A declaration is withdrawn by deleting it, never by dismissing a route.
        let declaredRow = contribution(
            "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
            occurrences: 0, declared: true, contributionID: "sc-1",
            sourceAccountID: "acct-checking", destinationAccountID: "acct-529")
        #expect(OverviewView.dismissableRoute(declaredRow) == nil)
    }

    // MARK: - #4 goal funding links

    private func goal(
        _ id: String, name: String, type: Components.Schemas.GoalType = .college
    ) -> Components.Schemas.Goal {
        .init(
            id: id, name: name, _type: type,
            target: money(1_000_000), current: money(250_000), priority: 1)
    }

    /// Accepting the "Fund <goal>?" suggestion PATCHes the link, then reloads
    /// so the row shows "funds <goal>" and the goal's funding line updates.
    @Test func linkingAContributionPatchesTheGoalThenRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.linkContribution(contributionID: "sc-1", goalID: "goal-college")

        #expect(api.links.count == 1)
        #expect(api.links.first?.id == "sc-1")
        #expect(api.links.first?.goalID == "goal-college")
        #expect(api.callCount == 1)  // reloaded the Overview after linking
        #expect(viewModel.errorMessage == nil)
    }

    /// Unlink is the same PATCH with a nil goal.
    @Test func unlinkingPatchesANilGoalThenRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.linkContribution(contributionID: "sc-1", goalID: nil)

        #expect(api.links.count == 1)
        #expect(api.links.first?.id == "sc-1")
        #expect(api.links.first?.goalID == nil)
        #expect(api.callCount == 1)
    }

    /// A refused link must say so and must NOT refresh — same contract as the
    /// other contribution writes.
    @Test func aRefusedLinkSurfacesTheErrorAndDoesNotRefresh() async {
        let api = MockHouseholdAPI()
        api.context = context()
        api.mutationError = APIError.unauthorized
        let viewModel = OverviewViewModel(api: api, notifications: nil, snapshotStore: nil)

        await viewModel.linkContribution(contributionID: "sc-1", goalID: "goal-college")

        #expect(api.callCount == 0)
        #expect(viewModel.errorMessage?.contains("pairing") == true)
    }

    /// Goal names load lazily via one goals-list fetch — only when a loaded
    /// contribution references a goal, and never per row.
    @Test func goalNamesLoadOnceWhenAContributionReferencesAGoal() async {
        let api = MockHouseholdAPI()
        api.context = context(contributions: [
            contribution(
                "College 529", amount: 50_000, frequency: .monthly, monthly: 50_000,
                occurrences: 0, declared: true, contributionID: "sc-1",
                goalID: "goal-college"),
            contribution(
                "Vanguard", amount: 20_000, frequency: .monthly, monthly: 20_000,
                occurrences: 0, declared: true, contributionID: "sc-2",
                suggestedGoalID: "goal-retire"),
        ])
        let goalsAPI = MockGoalsAPI()
        goalsAPI.result = [
            goal("goal-college", name: "College fund"),
            goal("goal-retire", name: "Retire at 60", type: .retirement),
        ]
        let viewModel = OverviewViewModel(
            api: api, goalsAPI: goalsAPI, notifications: nil, snapshotStore: nil)

        await viewModel.load()

        #expect(goalsAPI.callCount == 1)
        #expect(viewModel.goalNames["goal-college"] == "College fund")
        #expect(viewModel.goalNames["goal-retire"] == "Retire at 60")
    }

    /// No referenced goal, no fetch — most households pay for nothing here.
    @Test func goalNamesAreNotFetchedWhenNothingReferencesAGoal() async {
        let api = MockHouseholdAPI()
        api.context = context(contributions: [
            contribution(
                "Rainy Day Savings", amount: 20_000, frequency: .monthly, monthly: 20_000,
                occurrences: 6)
        ])
        let goalsAPI = MockGoalsAPI()
        let viewModel = OverviewViewModel(
            api: api, goalsAPI: goalsAPI, notifications: nil, snapshotStore: nil)

        await viewModel.load()

        #expect(goalsAPI.callCount == 0)
        #expect(viewModel.goalNames.isEmpty)
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
            monthlyExpenses: testQualified(100_000),
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


final class YearlyReviewConflictTransport: ClientTransport, @unchecked Sendable {
    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let payload = Data(
            #"{"error":{"code":"sealed_amount_unreadable","message":"A required amount is unreadable."}}"#.utf8)
        return (
            HTTPResponse(status: .conflict, headerFields: [.contentType: "application/json"]),
            HTTPBody(payload))
    }
}

struct YearlyReviewAdapterTests {
    @Test func typedConflictMapsToIncompleteData() async {
        let client = Client(
            serverURL: URL(string: "https://box.local")!,
            transport: YearlyReviewConflictTransport())
        let api = LiveHouseholdAPI(client: client)

        do {
            _ = try await api.generateYearlyReview(year: 2026)
            Issue.record("expected typed 409 to throw incompleteData")
        } catch {
            #expect(error as? APIError == .incompleteData)
        }
    }
}

@MainActor
struct YearlyOverviewViewModelTests {
    final class MockYearlyAPI: HouseholdAPI, @unchecked Sendable {
        var yearlyResult: Components.Schemas.YearlyOverview?
        var reviewResult: Components.Schemas.YearlyReview?
        private(set) var requestedYears: [Int?] = []

        nonisolated func context(month: String?) async throws -> Components.Schemas.HouseholdContext {
            throw APIError.server(500)
        }
        nonisolated func transactions(month: String?) async throws -> [Components.Schemas.Transaction] { [] }
        nonisolated func syncAll() async throws -> SyncTotals { SyncTotals() }
        nonisolated func spending(month: String?) async throws -> Components.Schemas.SpendingByCategory {
            throw APIError.server(500)
        }
        nonisolated func yearly(year: Int?) async throws -> Components.Schemas.YearlyOverview {
            await MainActor.run { requestedYears.append(year) }
            if let yearlyResult { return yearlyResult }
            throw APIError.server(500)
        }
        nonisolated func generateYearlyReview(year: Int?) async throws -> Components.Schemas.YearlyReview {
            if let reviewResult { return reviewResult }
            throw APIError.server(503)
        }
    }

    private func overview(year: Int) -> Components.Schemas.YearlyOverview {
        .init(
            year: year,
            months: [
                .init(
                    month: "\(year)-01",
                    income: testQualified(500_000),
                    spending: testQualified(300_000),
                    net: testQualified(200_000))
            ],
            totalIncome: testQualified(500_000),
            totalSpending: testQualified(300_000),
            totalNet: testQualified(200_000),
            topCategories: [])
    }

    @Test func loadsTheYearAndStepsBackwards() async {
        let api = MockYearlyAPI()
        api.yearlyResult = overview(year: 2026)
        let viewModel = YearlyOverviewViewModel(api: api)

        await viewModel.load()
        #expect(viewModel.overview?.year == 2026)

        api.yearlyResult = overview(year: 2025)
        await viewModel.step(-1)
        #expect(api.requestedYears.last == 2025)
    }

    @Test func generateReviewAttachesTheResult() async {
        let api = MockYearlyAPI()
        api.yearlyResult = overview(year: 2026)
        api.reviewResult = .init(
            summary: "A steady year.", suggestions: ["Trim subscriptions"],
            monthsCovered: 7, generatedAt: Date())
        let viewModel = YearlyOverviewViewModel(api: api)
        await viewModel.load()

        await viewModel.generateReview()

        #expect(viewModel.overview?.review?.summary == "A steady year.")
        #expect(viewModel.overview?.review?.suggestions == ["Trim subscriptions"])
    }

    // ADR 0068: the chart's "explain" buttons must ask the advisor with the
    // same wording on every client, naming the month unambiguously.
    @Test func explainQuestionsNameTheMonthInFull() {
        #expect(YearlyOverviewView.longLabel("2026-04") == "April 2026")
        #expect(
            YearlyOverviewView.incomeQuestion(for: "2026-04")
                == "What made up my income in April 2026? List where the money came from.")
        #expect(
            YearlyOverviewView.spendingQuestion(for: "2026-06")
                == "What made up my spending in June 2026? Break it down by category and biggest merchants.")
    }
}

/// #30: the cash outlook must never dress an estimate up as exact — only a
/// statement-backed row is the figure the issuer actually billed.
@MainActor
struct CashOutlookStatementTreatmentTests {
    private func event(source: String?) -> Components.Schemas.OutlookEvent {
        .init(
            occurredOn: "2026-08-12", name: "Sapphire",
            amount: .init(amountMinor: -128_450, currency: "USD"),
            kind: .creditCard, source: source)
    }

    @Test func onlyAStatementRowIsCalledExact() {
        #expect(CashOutlookDetailView.statementNote(event(source: "statement")) != nil)
        #expect(CashOutlookDetailView.statementNote(event(source: "estimate")) == nil)
        // An older box that doesn't send `source` at all is still an estimate.
        #expect(CashOutlookDetailView.statementNote(event(source: nil)) == nil)
    }

    /// The Bills timeline and the outlook mark exactness identically — one
    /// wording, so the two screens can't disagree about the same card.
    @Test func theOutlookReusesTheBillsTimelineWording() {
        let note = CashOutlookDetailView.statementNote(event(source: "statement")) ?? ""

        #expect(note.lowercased().contains("statement"))
        #expect(note.lowercased().contains("exact"))
        #expect(
            note
                == BillsView.statementNote(
                    .init(
                        id: "card-1", kind: .creditCard, name: "Sapphire",
                        amount: .init(amountMinor: 128_450, currency: "USD"),
                        dueDate: "2026-08-12", daysUntil: 4, status: .dueSoon,
                        source: "statement", statementId: nil)))
    }
}
