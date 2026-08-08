import Foundation
import Testing

@testable import FamilyCFO

/// #11: a credit card's statement cycles. The card's synced balance keeps
/// moving; a recorded statement is the exact amount, so the tests below pin the
/// two rules that matter — recording never stacks a second obligation for the
/// same money, and a scan only ever prefills.
@MainActor
final class MockAccountsAPI: AccountsAPI, @unchecked Sendable {
    var currentAccounts: [Components.Schemas.Account] = []
    var storedStatements: [Components.Schemas.CardStatement] = []
    var scanResult: Components.Schemas.CardStatementScanResult?
    var scanError: Error?
    var actionError: Error?
    /// #25: what the GET returns, keyed by statement id.
    var reconciliations: [String: Components.Schemas.StatementReconciliation] = [:]
    var reconciliationError: Error?

    private(set) var recorded: [CardStatementDraft] = []
    private(set) var paidMarks: [(id: String, paidAt: String?)] = []
    private(set) var deleted: [String] = []
    private(set) var listCalls = 0
    private(set) var scanCalls = 0
    private(set) var reconciliationCalls = 0
    private(set) var replacedLines:
        [(statementID: String, lines: [Components.Schemas.StatementLineInput])] = []

    nonisolated func accounts() async throws -> [Components.Schemas.Account] {
        await MainActor.run { currentAccounts }
    }

    nonisolated func setEmergencyFund(
        id: String, currency: String, _ designation: EmergencyFundDesignation
    ) async throws {}
    nonisolated func rename(id: String, name: String) async throws {}
    nonisolated func setType(id: String, type: Components.Schemas.AccountType) async throws {}
    nonisolated func setRsuReadyToSell(id: String, _ readyToSell: Bool) async throws {}
    nonisolated func syncBanks() async throws {}
    nonisolated func createManualAccount(
        name: String, type: Components.Schemas.AccountType, currency: String, balanceMinor: Int64
    ) async throws {}

    nonisolated func cardStatements(
        accountID: String
    ) async throws -> [Components.Schemas.CardStatement] {
        await MainActor.run {
            listCalls += 1
            return storedStatements.filter { $0.accountId == accountID }
        }
    }

    nonisolated func recordCardStatement(_ draft: CardStatementDraft) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            recorded.append(draft)
            let statement = Components.Schemas.CardStatement(
                id: "stmt-\(recorded.count)",
                accountId: draft.accountID,
                accountName: "Sapphire",
                statementBalance: .init(
                    amountMinor: draft.statementBalanceMinor, currency: draft.currency),
                minimumDue: draft.minimumDueMinor.map {
                    .init(amountMinor: $0, currency: draft.currency)
                },
                dueDate: draft.dueDate,
                periodStart: draft.periodStart,
                periodEnd: draft.periodEnd)
            // The server upserts on card + due date; the mock does the same so
            // the "same cycle twice" test exercises what really happens.
            if let index = storedStatements.firstIndex(where: {
                $0.accountId == draft.accountID && $0.dueDate == draft.dueDate
            }) {
                var updated = statement
                updated.id = storedStatements[index].id
                storedStatements[index] = updated
            } else {
                storedStatements.append(statement)
            }
        }
    }

    nonisolated func markCardStatementPaid(id: String, paidAt: String?) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            paidMarks.append((id, paidAt))
            if let index = storedStatements.firstIndex(where: { $0.id == id }) {
                storedStatements[index].paidAt = paidAt
            }
        }
    }

    nonisolated func deleteCardStatement(id: String) async throws {
        try await MainActor.run {
            if let actionError { throw actionError }
            deleted.append(id)
            storedStatements.removeAll { $0.id == id }
        }
    }

    nonisolated func scanCardStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.CardStatementScanResult {
        try await MainActor.run {
            scanCalls += 1
            if let scanError { throw scanError }
            guard let scanResult else { throw APIError.server(503) }
            return scanResult
        }
    }

    nonisolated func statementReconciliation(
        statementID: String
    ) async throws -> Components.Schemas.StatementReconciliation {
        try await MainActor.run {
            reconciliationCalls += 1
            if let reconciliationError { throw reconciliationError }
            guard let found = reconciliations[statementID] else { throw APIError.server(404) }
            return found
        }
    }

    nonisolated func replaceStatementLines(
        statementID: String, lines: [Components.Schemas.StatementLineInput]
    ) async throws -> Components.Schemas.StatementReconciliation {
        try await MainActor.run {
            if let actionError { throw actionError }
            replacedLines.append((statementID, lines))
            // The server replaces, never appends — so does the mock, and a
            // freshly stored line has matched nothing yet.
            let stored = Components.Schemas.StatementReconciliation(
                statementId: statementID,
                accountName: "Sapphire",
                periodLabel: "August 2026",
                lines: lines.enumerated().map { index, line in
                    .init(
                        id: "line-\(index)", occurredOn: line.occurredOn,
                        description: line.description, amount: line.amount)
                },
                unaccounted: [],
                matchedCount: 0,
                missingFromSyncCount: lines.count,
                notOnStatementCount: 0,
                amountDiffersCount: 0)
            reconciliations[statementID] = stored
            return stored
        }
    }
}

@MainActor
struct CardStatementsViewModelTests {
    private func card(
        id: String = "card-1", type: Components.Schemas.AccountType = .creditCard
    ) -> Components.Schemas.Account {
        .init(
            id: id, name: "Sapphire", _type: type,
            balance: .init(amountMinor: -84_213, currency: "USD"))
    }

    private func statement(
        id: String,
        dueDate: String,
        amountMinor: Int64 = 120_000,
        minimumMinor: Int64? = nil,
        periodStart: String? = nil,
        periodEnd: String? = nil,
        paidAt: String? = nil
    ) -> Components.Schemas.CardStatement {
        .init(
            id: id, accountId: "card-1", accountName: "Sapphire",
            statementBalance: .init(amountMinor: amountMinor, currency: "USD"),
            minimumDue: minimumMinor.map { .init(amountMinor: $0, currency: "USD") },
            dueDate: dueDate, periodStart: periodStart, periodEnd: periodEnd, paidAt: paidAt)
    }

    private func loaded() async -> (CardStatementsViewModel, MockAccountsAPI) {
        let api = MockAccountsAPI()
        let vm = CardStatementsViewModel(api: api, account: card())
        await vm.load()
        return (vm, api)
    }

    private func draft(
        amount: Double, dueDate: String, minimum: Double? = nil
    ) -> CardStatementsViewModel.Draft {
        var draft = CardStatementsViewModel.Draft()
        draft.amount = amount
        draft.dueDate = LoanDate.date(from: dueDate) ?? Date()
        if let minimum {
            draft.hasMinimum = true
            draft.minimum = minimum
        }
        return draft
    }

    @Test func loadListsTheCardsCyclesNewestFirst() async {
        let api = MockAccountsAPI()
        api.storedStatements = [
            statement(id: "old", dueDate: "2026-06-12"),
            statement(id: "new", dueDate: "2026-08-12"),
            .init(
                id: "other-card", accountId: "card-2", accountName: "Freedom",
                statementBalance: .init(amountMinor: 1_000, currency: "USD"),
                dueDate: "2026-09-01"),
        ]
        let vm = CardStatementsViewModel(api: api, account: card())

        await vm.load()

        #expect(vm.statements.map(\.id) == ["new", "old"])
        #expect(vm.errorMessage == nil)
    }

    @Test func recordingPostsTheCycleAndRefreshes() async {
        let (vm, api) = await loaded()

        let saved = await vm.record(draft(amount: 1_284.50, dueDate: "2026-08-12", minimum: 35))

        #expect(saved)
        #expect(api.recorded.count == 1)
        let posted = api.recorded[0]
        #expect(posted.accountID == "card-1")
        #expect(posted.currency == "USD")
        #expect(posted.statementBalanceMinor == 128_450)
        #expect(posted.dueDate == "2026-08-12")
        #expect(posted.minimumDueMinor == 3_500)
        #expect(posted.periodStart == nil)
        // The list is re-read after the POST, so the new cycle is on screen.
        #expect(api.listCalls == 2)
        #expect(vm.statements.map(\.dueDate) == ["2026-08-12"])
    }

    @Test func recordingTheSameCycleAgainUpdatesItRatherThanStacking() async {
        let (vm, api) = await loaded()
        _ = await vm.record(draft(amount: 1_284.50, dueDate: "2026-08-12"))

        _ = await vm.record(draft(amount: 1_310.00, dueDate: "2026-08-12"))

        #expect(api.recorded.count == 2)
        // Same card + due date is one cycle — never two obligations for the
        // same money.
        #expect(vm.statements.count == 1)
        #expect(vm.statements[0].statementBalance.amountMinor == 131_000)
    }

    @Test func markingPaidStampsTodayAndClearingWipesIt() async {
        let api = MockAccountsAPI()
        api.storedStatements = [statement(id: "s1", dueDate: "2026-08-12")]
        let vm = CardStatementsViewModel(api: api, account: card())
        await vm.load()

        await vm.setPaid(vm.statements[0], paid: true)
        #expect(api.paidMarks.count == 1)
        #expect(api.paidMarks[0].id == "s1")
        #expect(api.paidMarks[0].paidAt == LoanDate.iso(from: Date()))
        #expect(vm.statements[0].paidAt != nil)

        await vm.setPaid(vm.statements[0], paid: false)
        #expect(api.paidMarks.count == 2)
        #expect(api.paidMarks[1].paidAt == nil)
        #expect(vm.statements[0].paidAt == nil)
    }

    @Test func deletingRemovesTheCycle() async {
        let api = MockAccountsAPI()
        api.storedStatements = [
            statement(id: "s1", dueDate: "2026-08-12"),
            statement(id: "s2", dueDate: "2026-07-12"),
        ]
        let vm = CardStatementsViewModel(api: api, account: card())
        await vm.load()

        await vm.delete(vm.statements[0])

        #expect(api.deleted == ["s1"])
        #expect(vm.statements.map(\.id) == ["s2"])
    }

    @Test func aFailedActionSurfacesTheServersRefusal() async {
        let (vm, api) = await loaded()
        api.actionError = APIError.server(422)

        let saved = await vm.record(draft(amount: 100, dueDate: "2026-08-12"))

        #expect(!saved)
        #expect(vm.errorMessage?.contains("422") == true)
        #expect(vm.statements.isEmpty)
    }

    // MARK: - Scanning prefills, never saves

    @Test func scanResultPrefillsTheDraftAndSavesNothing() async throws {
        let (vm, api) = await loaded()
        api.scanResult = .init(
            statementBalanceMinor: 128_450,
            minimumDueMinor: 3_500,
            dueDate: "2026-08-12",
            periodStart: "2026-07-10",
            periodEnd: "2026-08-09",
            note: "Read the payment summary box.")

        let result = await vm.scan(fileData: Data("%PDF-1.4".utf8), isPDF: true)

        #expect(api.scanCalls == 1)
        // The scan is a read: nothing was recorded by it.
        #expect(api.recorded.isEmpty)
        #expect(vm.statements.isEmpty)

        var draft = CardStatementsViewModel.Draft()
        draft.apply(try #require(result))
        #expect(draft.amount == 1_284.50)
        #expect(draft.hasMinimum)
        #expect(draft.minimum == 35)
        #expect(LoanDate.iso(from: draft.dueDate) == "2026-08-12")
        #expect(draft.hasPeriod)
        #expect(LoanDate.iso(from: draft.periodStart) == "2026-07-10")
        #expect(LoanDate.iso(from: draft.periodEnd) == "2026-08-09")
    }

    @Test func scanNeverOverwritesWhatTheUserAlreadyEntered() {
        var draft = CardStatementsViewModel.Draft()
        draft.amount = 900
        draft.dueDate = LoanDate.date(from: "2026-08-20") ?? Date()
        draft.dueDateTouched = true

        draft.apply(
            .init(
                statementBalanceMinor: 128_450, minimumDueMinor: 3_500, dueDate: "2026-08-12",
                note: "note"))

        #expect(draft.amount == 900)
        #expect(LoanDate.iso(from: draft.dueDate) == "2026-08-20")
        // The minimum was untouched, so the reading is welcome there.
        #expect(draft.hasMinimum)
        #expect(draft.minimum == 35)
    }

    @Test func noVisionModelSaysSoInsteadOfAnUnexpectedStatus() async {
        let (vm, api) = await loaded()
        api.scanError = CardStatementScanError.visionModelUnavailable

        let result = await vm.scan(fileData: Data("%PDF-1.4".utf8), isPDF: true)

        #expect(result == nil)
        #expect(vm.errorMessage?.contains("vision model") == true)
        #expect(vm.errorMessage?.contains("503") != true)
    }

    // MARK: - Where the section shows up

    @Test func onlyCreditCardsOfferStatements() {
        #expect(AccountsViewModel.hasStatements(card()))
        #expect(!AccountsViewModel.hasStatements(card(type: .checking)))
        #expect(!AccountsViewModel.hasStatements(card(type: .mortgage)))
    }

    // MARK: - Row copy

    @Test func rowLinesReadTheRecordedCycleBack() {
        let unpaid = statement(
            id: "s1", dueDate: "2026-08-12", minimumMinor: 3_500,
            periodStart: "2026-07-10", periodEnd: "2026-08-09")
        #expect(CardStatementsViewModel.dueLine(unpaid).contains("Aug 12"))
        let detail = CardStatementsViewModel.detailLine(unpaid) ?? ""
        #expect(detail.contains("$35.00"))
        #expect(detail.contains("Jul 10"))

        let bare = statement(id: "s2", dueDate: "2026-08-12")
        #expect(CardStatementsViewModel.detailLine(bare) == nil)

        let paid = statement(id: "s3", dueDate: "2026-08-12", paidAt: "2026-08-10")
        #expect(CardStatementsViewModel.dueLine(paid).contains("Aug 10"))
    }
}

/// #11: the Due soon timeline must never dress an estimate up as exact.
@MainActor
struct PaymentTimelineStatementTreatmentTests {
    private func item(source: String?) -> Components.Schemas.PaymentTimelineItem {
        .init(
            id: "card-1", kind: .creditCard, name: "Sapphire",
            amount: .init(amountMinor: 128_450, currency: "USD"),
            dueDate: "2026-08-12", daysUntil: 4, source: source, statementId: nil,
            status: .dueSoon)
    }

    @Test func onlyAStatementRowIsCalledExact() {
        #expect(BillsView.statementNote(item(source: "statement")) != nil)
        #expect(BillsView.statementNote(item(source: "estimate")) == nil)
        // An older box that doesn't send `source` at all is still an estimate.
        #expect(BillsView.statementNote(item(source: nil)) == nil)
    }

    @Test func theStatementNoteSaysWhereTheFigureCameFrom() {
        let note = BillsView.statementNote(item(source: "statement")) ?? ""

        #expect(note.lowercased().contains("statement"))
        #expect(note.lowercased().contains("exact"))
    }
}
