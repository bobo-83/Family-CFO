import Foundation
import UIKit

/// Drives the Accounts tab (M99): every account grouped by kind, with balances
/// and emergency-fund designation.
@MainActor
@Observable
final class AccountsViewModel {
    private let api: AccountsAPI

    private(set) var accounts: [Components.Schemas.Account] = []
    private(set) var isLoading = false
    private(set) var isScanning = false
    var errorMessage: String?

    init(api: AccountsAPI) { self.api = api }

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

    /// Total emergency fund reserved across all accounts, in the base currency.
    var emergencyFundTotal: Components.Schemas.Money? {
        let reserved = accounts.compactMap(\.emergencyFundReserved)
        guard let currency = reserved.first?.currency else { return nil }
        let total = reserved.filter { $0.currency == currency }.reduce(Int64(0)) {
            $0 + $1.amountMinor
        }
        return .init(amountMinor: total, currency: currency)
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
    func syncAndReload() async {
        do {
            try await api.syncBanks()
            errorMessage = nil
        } catch {
            // A sync failure shouldn't hide existing accounts — still reload below.
            errorMessage = ChatViewModel.describe(error)
        }
        await load()
    }

    /// Currency for a new manual account — match what's already here, else USD.
    var defaultCurrency: String { accounts.first?.balance.currency ?? "USD" }

    func addAccount(
        name: String, type: Components.Schemas.AccountType, balanceMinor: Int64
    ) async {
        do {
            try await api.createManualAccount(
                name: name, type: type, currency: defaultCurrency, balanceMinor: balanceMinor)
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
