import Foundation
import UIKit

/// Drives the Accounts tab (M99): every account grouped by kind, with balances
/// and emergency-fund designation.
@MainActor
@Observable
final class AccountsViewModel {
    private let api: AccountsAPI
    /// #156: the household's base currency, resolved and cached per session.
    /// nil (an older preview or mock) leaves the Add button closed.
    private let currencyProvider: HouseholdCurrencyProvider?

    private(set) var accounts: [Components.Schemas.Account] = []
    private(set) var isLoading = false
    private(set) var isScanning = false
    var errorMessage: String?

    init(api: AccountsAPI, currency: HouseholdCurrencyProvider? = nil) {
        self.api = api
        self.currencyProvider = currency
    }

    /// #156 (ADR 0075): the base currency — nil until known, never a default.
    var baseCurrency: String? { currencyProvider?.current }
    var currencyError: String? { currencyProvider?.errorMessage }

    func loadCurrency() async {
        _ = try? await currencyProvider?.resolve()
    }

    /// Real and listed like any other account; counted in no base-currency
    /// total and never converted (#156, ADR 0075).
    func isOutsideBaseCurrency(_ account: Components.Schemas.Account) -> Bool {
        guard let base = baseCurrency else { return false }
        return account.balance.currency != base
    }

    struct Group: Identifiable {
        let id: String
        let title: String
        let accounts: [Components.Schemas.Account]
    }

    /// Accounts bucketed into the sections the tab shows, in display order.
    var groups: [Group] {
        // The `id` is the stable key; the title is only ever shown.
        let order: [(String, String, Set<Components.Schemas.AccountType>)] = [
            ("cash", String(localized: "Cash"), [.checking, .savings]),
            (
                "investments", String(localized: "Investments"),
                [.brokerage, .retirement, .hsa, ._529]
            ),
            ("cards", String(localized: "Credit cards"), [.creditCard]),
            (
                "loans", String(localized: "Loans"),
                [.mortgage, .autoLoan, .studentLoan, ._401kLoan, .otherLiability]
            ),
        ]
        var used = Set<String>()
        var result: [Group] = []
        for (id, title, types) in order {
            let members = accounts.filter { types.contains($0._type) }
            members.forEach { used.insert($0.id) }
            if !members.isEmpty { result.append(Group(id: id, title: title, accounts: members)) }
        }
        let rest = accounts.filter { !used.contains($0.id) }
        if !rest.isEmpty {
            result.append(Group(id: "other", title: String(localized: "Other"), accounts: rest))
        }
        return result
    }

    /// Total emergency fund reserved across all accounts, in the base currency
    /// ONLY — the same number the Overview's emergency-fund card shows (#156,
    /// ADR 0075). This used to take the first reservation's currency and drop
    /// the rest silently. nil until the base currency is known or when nothing
    /// in it is reserved.
    var emergencyFundTotal: Components.Schemas.Money? {
        guard let base = baseCurrency else { return nil }
        let total = accounts.compactMap(\.emergencyFundReserved)
            .filter { $0.currency == base }
            .reduce(Int64(0)) { $0 + $1.amountMinor }
        return total > 0 ? .init(amountMinor: total, currency: base) : nil
    }

    /// #156: reservations held in another currency — disclosed beside the
    /// total, never added to it. Keyed by the account's id: names are not
    /// unique, and two "Savings" rows must stay two rows (review of #158).
    var foreignReservations: [(id: String, name: String, reserved: Components.Schemas.Money)] {
        guard let base = baseCurrency else { return [] }
        return accounts.compactMap { account in
            guard let reserved = account.emergencyFundReserved, reserved.currency != base else {
                return nil
            }
            return (account.id, account.name, reserved)
        }
    }

    /// Only asset accounts can hold the emergency fund (a card/loan can't).
    static func canHoldEmergencyFund(_ account: Components.Schemas.Account) -> Bool {
        switch account._type {
        case .checking, .savings, .brokerage, .hsa, .otherAsset: return true
        default: return false
        }
    }

    /// The RSU tag is for asset accounts (a stock-plan account syncs as one);
    /// a card or loan balance can never be vested shares.
    static func canTagRsuReadyToSell(_ account: Components.Schemas.Account) -> Bool {
        manualAssetTypes.contains(account._type)
    }

    static func designation(_ account: Components.Schemas.Account) -> EmergencyFundDesignation {
        if let percent = account.emergencyFundPercent, percent >= 100 { return .wholeBalance }
        if let amount = account.emergencyFundAmount { return .amount(amount.amountMinor) }
        if account.emergencyFundPercent != nil { return .wholeBalance }  // partial % → treat as whole for the toggle
        return .none
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            accounts = try await api.accounts()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Pull-to-refresh: fetch fresh data from the banks first, so a newly-linked
    /// account appears here without hunting for a separate "Sync now" button.
    /// #158 review: the currency is retried too, so one failed context fetch
    /// never leaves Add disabled for as long as the tab stays open.
    func syncAndReload() async {
        do {
            try await api.syncBanks()
            errorMessage = nil
        } catch {
            // A sync failure shouldn't hide existing accounts — still reload below.
            errorMessage = ChatViewModel.describe(error)
        }
        async let accounts: () = load()
        async let currency: () = loadCurrency()
        _ = await (accounts, currency)
    }

    /// #11: only a credit card has statement cycles — a checking account's
    /// balance is just its balance.
    static func hasStatements(_ account: Components.Schemas.Account) -> Bool {
        account._type == .creditCard
    }

    /// #11: the statements screen for one card, sharing this tab's API client.
    func cardStatements(for account: Components.Schemas.Account) -> CardStatementsViewModel {
        CardStatementsViewModel(api: api, account: account)
    }

    /// A manual account is created in the household's base currency (#156):
    /// the first account's currency, or a literal "USD", was how a EUR
    /// household got USD accounts. With the currency unknown nothing is saved.
    func addAccount(
        name: String, type: Components.Schemas.AccountType, balanceMinor: Int64
    ) async {
        guard let provider = currencyProvider, let currency = try? await provider.resolve() else {
            errorMessage = String(
                localized: "The household currency isn't known yet, so nothing was saved.")
            return
        }
        do {
            try await api.createManualAccount(
                name: name, type: type, currency: currency, balanceMinor: balanceMinor)
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    // ADR 0057: read a statement photo into add-account candidates (the sheet
    // prefills from the result; nothing is saved until the user taps Save).
    func scanStatement(_ image: UIImage) async -> Components.Schemas.AccountScanResult? {
        guard let data = image.jpegData(compressionQuality: 0.9) else {
            errorMessage = String(localized: "That photo couldn't be processed.")
            return nil
        }
        return await scan { try AttachmentTranscoder.image(from: data, displayName: "Statement") }
    }

    func scanStatement(fileData: Data, isPDF: Bool) async -> Components.Schemas.AccountScanResult? {
        await scan {
            isPDF
                ? try AttachmentTranscoder.pdf(from: fileData, displayName: "Statement")
                : try AttachmentTranscoder.image(from: fileData, displayName: "Statement")
        }
    }

    private func scan(
        _ makeAttachment: () throws -> ChatAttachment
    ) async -> Components.Schemas.AccountScanResult? {
        guard !isScanning else { return nil }
        isScanning = true
        defer { isScanning = false }
        do {
            let result = try await api.scanStatement(makeAttachment())
            errorMessage = nil
            return result
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return nil
        }
    }

    /// Apply an edit from the detail sheet — rename and/or change the emergency-fund
    /// designation, whichever the user touched.
    func save(
        _ account: Components.Schemas.Account,
        name: String,
        type: Components.Schemas.AccountType? = nil,
        designation: EmergencyFundDesignation?,
        rsuReadyToSell: Bool? = nil
    ) async {
        do {
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty, trimmed != account.name {
                try await api.rename(id: account.id, name: trimmed)
            }
            if let type, type != account._type {
                try await api.setType(id: account.id, type: type)
            }
            if let designation, designation != Self.designation(account) {
                try await api.setEmergencyFund(
                    id: account.id, currency: account.balance.currency, designation)
            }
            // Only PATCH the tag when the user actually flipped it.
            if let rsuReadyToSell, rsuReadyToSell != (account.rsuReadyToSell ?? false) {
                try await api.setRsuReadyToSell(id: account.id, rsuReadyToSell)
            }
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
