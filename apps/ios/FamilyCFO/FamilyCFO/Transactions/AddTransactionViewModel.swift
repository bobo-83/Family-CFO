import Foundation
import Observation

/// #29: the add-transaction form, and the one place the ledger's sign
/// convention is applied.
///
/// Money spent is NEGATIVE in this ledger. The dashboard asks the user to type
/// the minus sign; on a phone that is a silent way to record a $40 refund as a
/// $40 purchase, so the form takes a positive amount and an explicit
/// expense/income choice and converts here. Both surfaces store the same row.
///
/// The form state lives on the view model rather than in `@State` so a refused
/// save can be pinned by a test: the error surfaces and nothing the user typed
/// is thrown away.
@MainActor
@Observable
final class AddTransactionViewModel {
    /// #25: values handed over by another screen — an unmatched statement line
    /// whose charge the bank feed never delivered. A PREFILL and nothing more:
    /// constructing this view model never writes, so the household still reads
    /// every field and presses Save itself.
    struct Prefill: Equatable {
        var accountID: String
        /// ISO "yyyy-MM-dd".
        var occurredOn: String
        /// In the ledger's convention — negative is a charge.
        var amountMinor: Int64
        var merchant: String
    }

    /// Which way the money went. The stored sign follows from this, so the user
    /// never types a minus sign.
    enum Direction: Hashable, CaseIterable {
        case expense
        case income

        var label: String {
            switch self {
            case .expense: return String(localized: "Expense")
            case .income: return String(localized: "Income")
            }
        }
    }

    private let api: AddTransactionAPI
    private let prefill: Prefill?

    // MARK: Form state

    var accountID: String?
    var date = Date()
    /// Always a POSITIVE magnitude in major units; `direction` carries the sign.
    var amount: Double?
    var direction: Direction = .expense
    var merchant = ""
    var note = ""
    var categoryID: String?

    // MARK: Loaded reference data

    private(set) var accounts: [Components.Schemas.Account] = []
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var isLoading = false
    private(set) var isSaving = false
    var errorMessage: String?

    init(api: AddTransactionAPI, prefill: Prefill? = nil) {
        self.api = api
        self.prefill = prefill
        guard let prefill else { return }
        accountID = prefill.accountID
        date = LoanDate.date(from: prefill.occurredOn) ?? Date()
        amount = Double(abs(prefill.amountMinor)) / 100
        direction = prefill.amountMinor < 0 ? .expense : .income
        merchant = prefill.merchant
    }

    /// True when another screen filled this in. The sheet says so out loud —
    /// a form that arrives populated must never read as a save already made.
    var isPrefilled: Bool { prefill != nil }

    /// Money is spent FROM an asset account or ON a card. A mortgage or auto
    /// loan is repaid, not spent from, so it is never offered — except when a
    /// prefill names it, where hiding the account would break the handoff.
    private func isSelectable(_ account: Components.Schemas.Account) -> Bool {
        if account.id == prefill?.accountID { return true }
        return manualAssetTypes.contains(account._type) || account._type == .creditCard
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let fetchedAccounts = api.accounts()
            async let fetchedCategories = api.categories()
            accounts = try await fetchedAccounts.filter(isSelectable)
            categories = try await fetchedCategories
                .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            // Only choose FOR the user when they (or a prefill) haven't: cash
            // most often leaves checking, so that is the useful first guess.
            if accountID == nil || !accounts.contains(where: { $0.id == accountID }) {
                accountID =
                    accounts.first { $0._type == .checking }?.id ?? accounts.first?.id
            }
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    var selectedAccount: Components.Schemas.Account? {
        accounts.first { $0.id == accountID }
    }

    /// The amount is posted in the ACCOUNT's currency — the server refuses any
    /// other, and a household's accounts are what define its currency.
    var currency: String { selectedAccount?.balance.currency ?? "USD" }

    /// The signed minor-unit amount that will be stored: negative for an
    /// expense. The single place the convention is applied.
    var amountMinor: Int64 {
        let magnitude = Int64((abs(amount ?? 0) * 100).rounded())
        return direction == .expense ? -magnitude : magnitude
    }

    var canSave: Bool { accountID != nil && amountMinor != 0 && !isSaving }

    /// Record it. Returns true on success so the sheet can dismiss; on failure
    /// the typed values stay exactly as they are, because a server refusal (a
    /// sealed household, a stale account id) is fixable without retyping.
    @discardableResult
    func save() async -> Bool {
        guard let accountID, amountMinor != 0 else {
            errorMessage = String(
                localized: "Choose an account and enter an amount before saving.")
            return false
        }
        guard !isSaving else { return false }
        isSaving = true
        defer { isSaving = false }
        do {
            try await api.create(
                NewTransaction(
                    accountID: accountID,
                    occurredOn: LoanDate.iso(from: date),
                    amountMinor: amountMinor,
                    currency: currency,
                    merchant: Self.trimmed(merchant),
                    description: Self.trimmed(note),
                    categoryID: categoryID))
            errorMessage = nil
            return true
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return false
        }
    }

    /// Optional free text: blank means "not stated", never an empty string.
    private static func trimmed(_ text: String) -> String? {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }
}
