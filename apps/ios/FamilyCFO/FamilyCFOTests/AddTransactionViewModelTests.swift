import Foundation
import Testing

@testable import FamilyCFO

/// #29: recording a transaction by hand.
///
/// Two rules carry the feature. The ledger stores spending as a NEGATIVE
/// amount, and the form never asks the user to type a minus sign — so the
/// conversion is pinned here, in both directions. And the #25 handoff is a
/// prefill: opening the sheet from an unmatched statement line must write
/// nothing at all until a person presses Save.
@MainActor
final class MockAddTransactionAPI: AddTransactionAPI, @unchecked Sendable {
    var currentAccounts: [Components.Schemas.Account] = []
    var currentCategories: [Components.Schemas.Category] = []
    var loadError: Error?
    var createError: Error?

    private(set) var created: [NewTransaction] = []
    private(set) var accountCalls = 0

    nonisolated func accounts() async throws -> [Components.Schemas.Account] {
        try await MainActor.run {
            if let loadError { throw loadError }
            accountCalls += 1
            return currentAccounts
        }
    }

    nonisolated func categories() async throws -> [Components.Schemas.Category] {
        try await MainActor.run {
            if let loadError { throw loadError }
            return currentCategories
        }
    }

    nonisolated func create(_ transaction: NewTransaction) async throws {
        try await MainActor.run {
            if let createError { throw createError }
            created.append(transaction)
        }
    }
}

@MainActor
struct AddTransactionViewModelTests {
    private func account(
        id: String = "chk-1",
        name: String = "Everyday checking",
        type: Components.Schemas.AccountType = .checking,
        currency: String = "USD"
    ) -> Components.Schemas.Account {
        .init(
            id: id, name: name, _type: type,
            balance: .init(amountMinor: 250_000, currency: currency))
    }

    private func loaded(
        accounts: [Components.Schemas.Account]? = nil,
        categories: [Components.Schemas.Category] = [],
        prefill: AddTransactionViewModel.Prefill? = nil
    ) async -> (AddTransactionViewModel, MockAddTransactionAPI) {
        let api = MockAddTransactionAPI()
        api.currentAccounts = accounts ?? [account()]
        api.currentCategories = categories
        let viewModel = AddTransactionViewModel(api: api, prefill: prefill)
        await viewModel.load()
        return (viewModel, api)
    }

    // MARK: - The sign convention

    @Test func anExpenseIsStoredAsMoneyOut() async {
        let (viewModel, api) = await loaded(
            categories: [.init(id: "cat-1", name: "Groceries")])
        viewModel.amount = 12.5
        viewModel.direction = .expense
        viewModel.date = LoanDate.date(from: "2026-08-03") ?? Date()
        viewModel.merchant = "  Corner Market  "
        viewModel.note = "cash"
        viewModel.categoryID = "cat-1"

        #expect(await viewModel.save())

        #expect(api.created.count == 1)
        let posted = api.created[0]
        // The user typed 12.50 and chose Expense; the ledger's convention is
        // applied here and nowhere else.
        #expect(posted.amountMinor == -1_250)
        #expect(posted.accountID == "chk-1")
        // occurred_at is a DATE on the contract, so an ISO day and no clock.
        #expect(posted.occurredOn == "2026-08-03")
        #expect(posted.currency == "USD")
        #expect(posted.merchant == "Corner Market")
        #expect(posted.description == "cash")
        #expect(posted.categoryID == "cat-1")
        #expect(viewModel.errorMessage == nil)
    }

    @Test func moneyComingInIsStoredPositive() async {
        let (viewModel, api) = await loaded()
        viewModel.amount = 40
        viewModel.direction = .income

        #expect(await viewModel.save())

        #expect(api.created.map(\.amountMinor) == [4_000])
    }

    @Test func aTypedMinusSignCannotFlipTheDirectionChosen() async {
        let (viewModel, api) = await loaded()
        // The keypad can still produce a negative; the control is the only
        // thing that decides the sign, so a "-40 expense" is still money out.
        viewModel.amount = -40
        viewModel.direction = .expense

        #expect(await viewModel.save())

        #expect(api.created.map(\.amountMinor) == [-4_000])
    }

    // MARK: - Validation

    @Test func anEmptyAmountIsRefusedWithoutTouchingTheServer() async {
        let (viewModel, api) = await loaded()
        viewModel.amount = nil

        #expect(!(await viewModel.save()))

        #expect(api.created.isEmpty)
        #expect(!viewModel.canSave)
        #expect(viewModel.errorMessage != nil)
    }

    @Test func withNoAccountThereIsNothingToRecordAgainst() async {
        let (viewModel, api) = await loaded(accounts: [])
        viewModel.amount = 12

        #expect(viewModel.accountID == nil)
        #expect(!viewModel.canSave)
        #expect(!(await viewModel.save()))
        #expect(api.created.isEmpty)
    }

    @Test func moneyIsSpentFromAccountsNotRepaidLoans() async {
        let (viewModel, _) = await loaded(accounts: [
            account(id: "chk-1"),
            account(id: "card-1", name: "Sapphire", type: .creditCard),
            // A mortgage is repaid, never spent from — offering it would invite
            // a charge against a balance that can't hold one.
            account(id: "mtg-1", name: "House", type: .mortgage),
        ])

        #expect(viewModel.accounts.map(\.id) == ["chk-1", "card-1"])
        // Cash most often leaves checking, so that's the useful first guess.
        #expect(viewModel.accountID == "chk-1")
    }

    @Test func theAmountIsPostedInTheChosenAccountsCurrency() async {
        let (viewModel, api) = await loaded(accounts: [
            account(id: "chk-1"),
            account(id: "eur-1", name: "Vilnius savings", type: .savings, currency: "EUR"),
        ])
        viewModel.accountID = "eur-1"
        viewModel.amount = 9

        #expect(viewModel.currency == "EUR")
        #expect(await viewModel.save())
        // The server refuses any currency but the account's own.
        #expect(api.created.map(\.currency) == ["EUR"])
    }

    // MARK: - Failure

    @Test func aRefusedSaveSurfacesTheErrorAndKeepsWhatWasTyped() async {
        let (viewModel, api) = await loaded()
        viewModel.amount = 12.5
        viewModel.merchant = "Corner Market"
        viewModel.note = "cash"
        api.createError = APIError.server(403)

        #expect(!(await viewModel.save()))

        #expect(viewModel.errorMessage?.contains("403") == true)
        // Retyping a refused entry on a phone is how people give up on it.
        #expect(viewModel.amount == 12.5)
        #expect(viewModel.merchant == "Corner Market")
        #expect(viewModel.note == "cash")
        #expect(viewModel.canSave)
    }

    // MARK: - #25: closing a gap the statement found

    private func line(
        id: String = "line-1",
        description: String = "Blue Bottle",
        amountMinor: Int64 = -1_250,
        matchedTransactionID: String? = nil,
        matchKind: String? = nil
    ) -> Components.Schemas.StatementLine {
        .init(
            id: id, occurredOn: "2026-08-01", description: description,
            amount: .init(amountMinor: amountMinor, currency: "USD"),
            matchedTransactionId: matchedTransactionID, matchKind: matchKind)
    }

    private func reconciliationModel() -> StatementReconciliationViewModel {
        StatementReconciliationViewModel(
            api: MockAccountsAPI(),
            statement: .init(
                id: "stmt-1", accountId: "card-1", accountName: "Sapphire",
                statementBalance: .init(amountMinor: 120_000, currency: "USD"),
                dueDate: "2026-08-12"))
    }

    @Test func theGapOfferIsOnlyOnLinesTheFeedNeverDelivered() {
        #expect(StatementReconciliationViewModel.canAdd(line()))
        // Already in the ledger — adding it again would be the duplicate the
        // Review queue exists to catch.
        #expect(
            !StatementReconciliationViewModel.canAdd(
                line(matchedTransactionID: "txn-1", matchKind: "exact")))
        // A disagreement over the amount is a correction, not a missing row.
        #expect(
            !StatementReconciliationViewModel.canAdd(
                line(matchedTransactionID: "txn-2", matchKind: "amount_differs")))
    }

    @Test func openingThePrefilledSheetFillsTheFormAndWritesNothing() async {
        let api = MockAddTransactionAPI()
        api.currentAccounts = [account(id: "card-1", name: "Sapphire", type: .creditCard)]
        let prefill = reconciliationModel().prefill(for: line())

        let viewModel = AddTransactionViewModel(api: api, prefill: prefill)
        await viewModel.load()

        // Every value the statement knew, waiting to be confirmed.
        #expect(viewModel.isPrefilled)
        #expect(viewModel.accountID == "card-1")
        #expect(LoanDate.iso(from: viewModel.date) == "2026-08-01")
        #expect(viewModel.merchant == "Blue Bottle")
        #expect(viewModel.amount == 12.5)
        // The stored line is already negative, so the sheet lands on Expense
        // and re-signs to the same figure.
        #expect(viewModel.direction == .expense)
        #expect(viewModel.amountMinor == -1_250)
        // THE POINT: reconciliation never writes. Opening the sheet — even
        // fully populated — records nothing until a person presses Save.
        #expect(api.created.isEmpty)
    }

    @Test func aPrefilledSheetStillTakesAnExplicitSave() async {
        let api = MockAddTransactionAPI()
        api.currentAccounts = [account(id: "card-1", name: "Sapphire", type: .creditCard)]
        let viewModel = AddTransactionViewModel(
            api: api, prefill: reconciliationModel().prefill(for: line()))
        await viewModel.load()

        #expect(await viewModel.save())

        #expect(api.created.count == 1)
        #expect(api.created[0].accountID == "card-1")
        #expect(api.created[0].amountMinor == -1_250)
        #expect(api.created[0].merchant == "Blue Bottle")
        #expect(api.created[0].occurredOn == "2026-08-01")
    }

    @Test func aRefundOnTheStatementArrivesAsMoneyIn() async {
        let api = MockAddTransactionAPI()
        api.currentAccounts = [account(id: "card-1", name: "Sapphire", type: .creditCard)]
        // A statement credit is stored positive, so the sheet must not call it
        // an expense — that would double the household's loss.
        let prefill = reconciliationModel().prefill(
            for: line(description: "Resy credit", amountMinor: 5_000))

        let viewModel = AddTransactionViewModel(api: api, prefill: prefill)
        await viewModel.load()

        #expect(viewModel.direction == .income)
        #expect(viewModel.amountMinor == 5_000)
        #expect(api.created.isEmpty)
    }
}
