import Foundation
import Testing

@testable import FamilyCFO

/// #25: reconciling a statement's LINE ITEMS against the synced ledger.
///
/// The unmatched lines are the whole point — a charge the card printed that the
/// bank feed never delivered is a hole in the household's picture. So the tests
/// below pin what makes that finding legible: the coverage summary reads as a
/// finding rather than a form, and the gaps are never buried under the rows
/// that are fine.
@MainActor
struct StatementReconciliationViewModelTests {
    private func card() -> Components.Schemas.Account {
        .init(
            id: "card-1", name: "Sapphire", _type: .creditCard,
            balance: .init(amountMinor: -84_213, currency: "USD"))
    }

    private func statement(id: String = "stmt-1") -> Components.Schemas.CardStatement {
        .init(
            id: id, accountId: "card-1", accountName: "Sapphire",
            statementBalance: .init(amountMinor: 120_000, currency: "USD"),
            dueDate: "2026-08-12")
    }

    private func line(
        id: String,
        occurredOn: String,
        description: String = "Blue Bottle",
        amountMinor: Int64 = -1_250,
        matchedTransactionID: String? = nil,
        matchKind: String? = nil
    ) -> Components.Schemas.StatementLine {
        .init(
            id: id, occurredOn: occurredOn, description: description,
            amount: .init(amountMinor: amountMinor, currency: "USD"),
            matchedTransactionId: matchedTransactionID, matchKind: matchKind)
    }

    private func reconciliation(
        lines: [Components.Schemas.StatementLine] = [],
        unaccounted: [Components.Schemas.UnaccountedTransaction] = [],
        matched: Int = 0,
        missing: Int = 0,
        notOnStatement: Int = 0,
        differs: Int = 0
    ) -> Components.Schemas.StatementReconciliation {
        .init(
            statementId: "stmt-1", accountName: "Sapphire", periodLabel: "August 2026",
            lines: lines, unaccounted: unaccounted, matchedCount: matched,
            missingFromSyncCount: missing, notOnStatementCount: notOnStatement,
            amountDiffersCount: differs)
    }

    // MARK: - What a match means

    @Test func aLineWithNoTransactionIsAGapWhateverTheKindSays() {
        typealias State = StatementReconciliationViewModel.MatchState
        #expect(
            StatementReconciliationViewModel.state(of: line(id: "a", occurredOn: "2026-08-01"))
                == State.missing)
        #expect(
            StatementReconciliationViewModel.state(
                of: line(
                    id: "b", occurredOn: "2026-08-01", matchedTransactionID: "txn-1",
                    matchKind: "exact")) == State.matched)
        #expect(
            StatementReconciliationViewModel.state(
                of: line(
                    id: "c", occurredOn: "2026-08-01", matchedTransactionID: "txn-2",
                    matchKind: "amount_differs")) == State.amountDiffers)
        // An older box that sends a kind this build doesn't know still matched
        // something — treating it as a gap would invent an alarm.
        #expect(
            StatementReconciliationViewModel.state(
                of: line(
                    id: "d", occurredOn: "2026-08-01", matchedTransactionID: "txn-3",
                    matchKind: "something_new")) == State.matched)
    }

    // MARK: - Unmatched first

    @Test func gapsLeadAndMatchedChargesTrail() {
        let ordered = StatementReconciliationViewModel.ordered([
            line(id: "matched-late", occurredOn: "2026-08-05", matchedTransactionID: "t1"),
            line(id: "gap-late", occurredOn: "2026-08-06"),
            line(
                id: "differs", occurredOn: "2026-08-02", matchedTransactionID: "t2",
                matchKind: "amount_differs"),
            line(id: "matched-early", occurredOn: "2026-08-01", matchedTransactionID: "t3"),
            line(id: "gap-early", occurredOn: "2026-08-03"),
        ])

        // Gaps, then near-misses, then the rows that are fine — and each group
        // oldest first so a cycle still reads chronologically.
        #expect(
            ordered.map(\.id) == [
                "gap-early", "gap-late", "differs", "matched-early", "matched-late",
            ])
    }

    // MARK: - The coverage summary

    @Test func theCoverageSummaryNamesEveryFinding() {
        let found = reconciliation(matched: 2, missing: 1, notOnStatement: 1, differs: 1)
        let coverage = StatementReconciliationViewModel.coverage(found)

        // Matched + missing is every stored line, so no separate total is sent.
        #expect(coverage.total == 3)

        let summary = StatementReconciliationViewModel.coverageSummary(coverage)
        #expect(summary.contains("2 of 3 charges matched"))
        #expect(summary.contains("1 not synced"))
        #expect(summary.contains("1 amount differs"))
        #expect(summary.contains("1 posted after close"))
    }

    @Test func aCleanStatementSaysOnlyThat() {
        let coverage = StatementReconciliationViewModel.coverage(
            reconciliation(matched: 4, missing: 0))

        let summary = StatementReconciliationViewModel.coverageSummary(coverage)

        #expect(summary == "4 of 4 charges matched")
        // Zeros are findings that aren't there — never printed as "0 not synced".
        #expect(!summary.contains("0"))
    }

    // MARK: - Loading

    @Test func loadingSortsTheLinesAndKeepsTheExtrasApart() async {
        let api = MockAccountsAPI()
        api.reconciliations["stmt-1"] = reconciliation(
            lines: [
                line(id: "matched", occurredOn: "2026-08-01", matchedTransactionID: "t1"),
                line(id: "gap", occurredOn: "2026-08-04", description: "Ferry Building"),
            ],
            unaccounted: [
                .init(
                    transactionId: "txn-9", occurredAt: "2026-08-11", merchant: "Costco",
                    amount: .init(amountMinor: -9_900, currency: "USD"))
            ],
            matched: 1, missing: 1, notOnStatement: 1)
        let vm = StatementReconciliationViewModel(api: api, statement: statement())

        await vm.load()

        #expect(api.reconciliationCalls == 1)
        #expect(vm.lines.map(\.id) == ["gap", "matched"])
        #expect(vm.unaccounted.map(\.transactionId) == ["txn-9"])
        #expect(vm.subtitle == "Sapphire · August 2026")
        #expect(!vm.hasNoStoredLines)
        #expect(vm.errorMessage == nil)
    }

    @Test func aStatementWithNothingReadOffItSaysSoRatherThanLookingClean() async {
        let api = MockAccountsAPI()
        api.reconciliations["stmt-1"] = reconciliation()
        let vm = StatementReconciliationViewModel(api: api, statement: statement())

        await vm.load()

        // Zero of zero matched would read as "all clear" — it isn't.
        #expect(vm.hasNoStoredLines)
        #expect(vm.lines.isEmpty)
    }

    @Test func aFailedReadSurfacesTheRefusalInsteadOfAnEmptyCheck() async {
        let api = MockAccountsAPI()
        api.reconciliationError = APIError.server(404)
        let vm = StatementReconciliationViewModel(api: api, statement: statement())

        await vm.load()

        #expect(vm.reconciliation == nil)
        #expect(vm.errorMessage?.contains("404") == true)
        // Nothing loaded, so the empty state must not claim the statement is bare.
        #expect(!vm.hasNoStoredLines)
    }

    // MARK: - Storing what the scan read

    private func scanResult(
        lines: [Components.Schemas.CardStatementScanLine]
    ) -> Components.Schemas.CardStatementScanResult {
        .init(
            statementBalanceMinor: 120_000, dueDate: "2026-08-12", lines: lines,
            note: "Read the transaction table.")
    }

    private func scanned() async -> (CardStatementsViewModel, MockAccountsAPI) {
        let api = MockAccountsAPI()
        api.scanResult = scanResult(lines: [
            .init(occurredOn: "2026-08-01", description: "Blue Bottle", amountMinor: -1_250),
            .init(occurredOn: "2026-08-04", description: "Ferry Building", amountMinor: -3_800),
            // No date means nothing to match against — it would read as a
            // permanent gap, so it never gets stored.
            .init(description: "Illegible row", amountMinor: -500),
        ])
        let vm = CardStatementsViewModel(api: api, account: card())
        await vm.load()
        _ = await vm.scan(fileData: Data("%PDF-1.4".utf8), isPDF: true)
        return (vm, api)
    }

    private func draft(dueDate: String = "2026-08-12") -> CardStatementsViewModel.Draft {
        var draft = CardStatementsViewModel.Draft()
        draft.amount = 1_200
        draft.dueDate = LoanDate.date(from: dueDate) ?? Date()
        return draft
    }

    @Test func aScanHoldsTheLineItemsButStoresNothingUntilTheCycleExists() async {
        let (vm, api) = await scanned()

        // The scan is a read: nothing has been stored, and there's no cycle for
        // the lines to hang off yet.
        #expect(vm.hasScannedLines)
        #expect(vm.scannedLines.count == 2)
        #expect(!vm.canStoreScannedLines)
        #expect(api.replacedLines.isEmpty)

        _ = await vm.record(draft())

        #expect(vm.canStoreScannedLines)
        #expect(vm.scannedLinesStatementID == "stmt-1")
        // Recording the cycle still stored no lines — that's a separate yes.
        #expect(api.replacedLines.isEmpty)
    }

    @Test func storingTheScannedLinesCallsTheAPIAndRefreshes() async {
        let (vm, api) = await scanned()
        _ = await vm.record(draft())
        let listCallsBefore = api.listCalls

        await vm.storeScannedLines()

        #expect(api.replacedLines.count == 1)
        let put = api.replacedLines[0]
        #expect(put.statementID == "stmt-1")
        #expect(put.lines.map(\.description) == ["Blue Bottle", "Ferry Building"])
        #expect(put.lines.map(\.occurredOn) == ["2026-08-01", "2026-08-04"])
        // Already in the ledger's convention: negative is a charge.
        #expect(put.lines.map(\.amount.amountMinor) == [-1_250, -3_800])
        #expect(put.lines.allSatisfy { $0.amount.currency == "USD" })
        // The screen is re-read after the write, and the offer is spent.
        #expect(api.listCalls == listCallsBefore + 1)
        #expect(!vm.hasScannedLines)
        #expect(!vm.canStoreScannedLines)
        #expect(vm.errorMessage == nil)
    }

    @Test func aRefusedStoreSurfacesTheErrorAndKeepsTheOfferOpen() async {
        let (vm, api) = await scanned()
        _ = await vm.record(draft())
        api.actionError = APIError.server(403)

        await vm.storeScannedLines()

        #expect(vm.errorMessage?.contains("403") == true)
        // The read wasn't thrown away, so the household can try again rather
        // than re-scan the statement.
        #expect(vm.canStoreScannedLines)
    }

    @Test func discardingThrowsTheReadAwayAndTouchesNothing() async {
        let (vm, api) = await scanned()
        _ = await vm.record(draft())

        vm.discardScannedLines()

        #expect(!vm.hasScannedLines)
        #expect(vm.scannedLinesStatementID == nil)
        #expect(api.replacedLines.isEmpty)
    }

    @Test func deletingTheCycleTakesItsPendingLinesWithIt() async {
        let (vm, _) = await scanned()
        _ = await vm.record(draft())

        await vm.delete(vm.statements[0])

        // Lines can't outlive the cycle they were read from.
        #expect(!vm.canStoreScannedLines)
    }

    @Test func aSecondScanReplacesTheFirstReadRatherThanDoublingIt() async {
        let (vm, api) = await scanned()
        api.scanResult = scanResult(lines: [
            .init(occurredOn: "2026-08-09", description: "Corrected read", amountMinor: -2_000)
        ])

        _ = await vm.scan(fileData: Data("%PDF-1.4".utf8), isPDF: true)

        #expect(vm.scannedLines.map(\.description) == ["Corrected read"])
    }
}
