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
                    categorizedTotal: .init(amountMinor: 0, currency: "USD"),
                    uncategorized: .init(amountMinor: 0, currency: "USD"))
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
    nonisolated func createGoal(_ request: Components.Schemas.GoalCreateRequest) async throws {}
    nonisolated func updateGoal(
        id: String, _ request: Components.Schemas.GoalUpdateRequest
    ) async throws {}
    nonisolated func deleteGoal(id: String) async throws {}
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
            displayName: "The Vus",
            currency: "USD",
            netWorth: money(1_234_500),
            emergencyFundMonths: 4.5,
            savingsContributions: contributions
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
            monthlyIncome: .init(amountMinor: 800_000, currency: "USD"),
            averageMonthlySpending: .init(amountMinor: 500_000, currency: "USD"),
            transfers: transfers.map { .init(amountMinor: $0, currency: "USD") },
            payrollDeductions: payroll.map { .init(amountMinor: $0, currency: "USD") },
            residual: residual.map { .init(amountMinor: $0, currency: "USD") },
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
                    income: .init(amountMinor: 500_000, currency: "USD"),
                    spending: .init(amountMinor: 300_000, currency: "USD"),
                    net: .init(amountMinor: 200_000, currency: "USD"))
            ],
            totalIncome: .init(amountMinor: 500_000, currency: "USD"),
            totalSpending: .init(amountMinor: 300_000, currency: "USD"),
            totalNet: .init(amountMinor: 200_000, currency: "USD"),
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
