import Foundation

/// The generated `Account` already carries a stable `id`; conforming lets it drive
/// `.sheet(item:)` directly.
extension Components.Schemas.Account: Identifiable {}

/// All accounts and their emergency-fund designation (M99). The Accounts tab
/// shows where the money is and lets the owner mark which accounts (or how much
/// of them) make up the emergency fund that safe-to-spend holds back.
protocol AccountsAPI: Sendable {
    func accounts() async throws -> [Components.Schemas.Account]
    func setEmergencyFund(
        id: String, currency: String, _ designation: EmergencyFundDesignation
    ) async throws
    /// Rename an account so generic bank labels ("Equity Awards") can be told
    /// apart. A user-set name survives future syncs.
    func rename(id: String, name: String) async throws
    func setType(id: String, type: Components.Schemas.AccountType) async throws
    /// Tag/untag this account's balance as vested RSUs ready to sell — surfaced
    /// beside safe-to-spend, never added to it.
    func setRsuReadyToSell(id: String, _ readyToSell: Bool) async throws
    /// Pull fresh data from the linked banks (SimpleFIN) — so a newly-added
    /// account shows up on pull-to-refresh, not only after a manual sync.
    func syncBanks() async throws
    /// Add an account by hand — for holdings a bank feed can't reach (e.g. a
    /// Everhealth HSA). Creates the account and records its current balance.
    func createManualAccount(
        name: String, type: Components.Schemas.AccountType, currency: String, balanceMinor: Int64
    ) async throws
    /// ADR 0057: read a statement photo/PDF into add-account candidates.
    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.AccountScanResult
    /// #11: the statement cycles recorded against one credit card.
    func cardStatements(accountID: String) async throws -> [Components.Schemas.CardStatement]
    /// #11: record a cycle — the EXACT amount the card asks for and the day it's
    /// due. Recording the same card + due date again UPDATES that cycle rather
    /// than stacking a second obligation for the same money.
    func recordCardStatement(_ draft: CardStatementDraft) async throws
    /// #11: mark a cycle settled on an ISO day, or pass nil to clear the mark.
    func markCardStatementPaid(id: String, paidAt: String?) async throws
    func deleteCardStatement(id: String) async throws
    /// #11: read a card statement photo/PDF into candidate values. Nothing is
    /// saved by the scan — the user confirms every figure first.
    func scanCardStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.CardStatementScanResult
}

extension AccountsAPI {
    /// Defaults so mocks/tests needn't implement them; the live client overrides.
    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.AccountScanResult {
        throw APIError.server(501)
    }
    func cardStatements(accountID: String) async throws -> [Components.Schemas.CardStatement] {
        throw APIError.server(501)
    }
    func recordCardStatement(_ draft: CardStatementDraft) async throws {
        throw APIError.server(501)
    }
    func markCardStatementPaid(id: String, paidAt: String?) async throws {
        throw APIError.server(501)
    }
    func deleteCardStatement(id: String) async throws {
        throw APIError.server(501)
    }
    func scanCardStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.CardStatementScanResult {
        throw APIError.server(501)
    }
}

/// #11: one credit-card cycle as the household states it — what the statement
/// says is due, and when. Held apart from the synced running balance, which
/// includes spending posted after the cycle closed.
struct CardStatementDraft: Equatable {
    var accountID: String
    var currency: String
    /// What the statement says you owe for the cycle, in minor units.
    var statementBalanceMinor: Int64
    /// ISO "yyyy-MM-dd".
    var dueDate: String
    var minimumDueMinor: Int64?
    var periodStart: String?
    var periodEnd: String?
}

/// #11: card-statement scan failures that deserve their own words — the generic
/// "unexpected status" tells the user nothing about what to do next.
enum CardStatementScanError: Error, LocalizedError, Equatable {
    case unsupportedFile
    case visionModelUnavailable

    var errorDescription: String? {
        switch self {
        case .unsupportedFile:
            return String(
                localized: "That file can't be scanned — use a photo or a PDF of the statement.")
        case .visionModelUnavailable:
            return String(
                localized:
                    "The box's vision model isn't running, so it can't read the statement. You can still type the figures in below."
            )
        }
    }
}

/// The generated `CardStatement` carries a stable `id`; conforming lets it drive
/// `ForEach` and `.sheet(item:)` directly.
extension Components.Schemas.CardStatement: Identifiable {}

/// Asset account types offered when adding one by hand (liabilities are the
/// Debts tab's job).
let manualAssetTypes: [Components.Schemas.AccountType] = [
    .checking, .savings, .hsa, .brokerage, .retirement, ._529, .realEstate, .otherAsset,
]

/// How much of an account counts as emergency fund.
enum EmergencyFundDesignation: Equatable {
    case none
    case wholeBalance  // 100%
    case amount(Int64)  // a fixed reserve, in minor units
}

struct LiveAccountsAPI: AccountsAPI {
    let client: Client

    func accounts() async throws -> [Components.Schemas.Account] {
        switch try await client.listAccounts(.init()) {
        case .ok(let response):
            return try response.body.json.accounts
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func setEmergencyFund(
        id: String, currency: String, _ designation: EmergencyFundDesignation
    ) async throws {
        let request: Components.Schemas.AccountUpdateRequest
        switch designation {
        case .none:
            request = .init(clearEmergencyFund: true)
        case .wholeBalance:
            request = .init(emergencyFundPercent: 100)
        case .amount(let minor):
            request = .init(
                emergencyFundAmount: .init(amountMinor: minor, currency: currency))
        }
        try await patch(id: id, request)
    }

    func rename(id: String, name: String) async throws {
        try await patch(id: id, .init(name: name))
    }

    func setRsuReadyToSell(id: String, _ readyToSell: Bool) async throws {
        // A bare boolean PATCH — omitted fields leave every other designation alone.
        try await patch(id: id, .init(rsuReadyToSell: readyToSell))
    }

    /// Corrects a mis-inferred type (user report 2026-07-25: a SimpleFIN loan
    /// landed as checking). The sync never re-types, so the correction sticks.
    func setType(id: String, type: Components.Schemas.AccountType) async throws {
        try await patch(id: id, .init(_type: type))
    }

    func syncBanks() async throws {
        switch try await client.syncAllConnections(.init()) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func createManualAccount(
        name: String, type: Components.Schemas.AccountType, currency: String, balanceMinor: Int64
    ) async throws {
        let request = Components.Schemas.AccountCreateRequest(
            name: name, _type: type, currency: currency)
        let created: Components.Schemas.Account
        switch try await client.createAccount(.init(body: .json(request))) {
        case .created(let response):
            created = try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
        let balance = Components.Schemas.AccountBalanceCreateRequest(
            balance: .init(amountMinor: balanceMinor, currency: currency))
        switch try await client.recordAccountBalance(
            .init(path: .init(accountId: created.id), body: .json(balance))
        ) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.AccountScanResult {
        guard case .visual(let mediaType) = attachment.kind,
            let scanMediaType = Components.Schemas.AccountScanRequest.ImageMediaTypePayload(
                rawValue: mediaType.rawValue)
        else {
            throw APIError.server(415)
        }
        let request = Components.Schemas.AccountScanRequest(
            imageBase64: attachment.data.base64EncodedString(),
            imageMediaType: scanMediaType
        )
        switch try await client.scanAccountStatement(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .unprocessableContent:
            throw APIError.server(422)
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func cardStatements(accountID: String) async throws -> [Components.Schemas.CardStatement] {
        switch try await client.listCardStatements(.init(query: .init(accountId: accountID))) {
        case .ok(let response):
            return try response.body.json.statements ?? []
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func recordCardStatement(_ draft: CardStatementDraft) async throws {
        let request = Components.Schemas.CardStatementCreateRequest(
            accountId: draft.accountID,
            statementBalance: .init(
                amountMinor: draft.statementBalanceMinor, currency: draft.currency),
            dueDate: draft.dueDate,
            minimumDue: draft.minimumDueMinor.map {
                .init(amountMinor: $0, currency: draft.currency)
            },
            periodStart: draft.periodStart,
            periodEnd: draft.periodEnd
        )
        switch try await client.recordCardStatement(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .unprocessableContent:
            // The server refuses a statement on anything but a credit card.
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func markCardStatementPaid(id: String, paidAt: String?) async throws {
        switch try await client.markCardStatementPaid(
            .init(path: .init(statementId: id), body: .json(.init(paidAt: paidAt)))
        ) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteCardStatement(id: String) async throws {
        switch try await client.deleteCardStatement(.init(path: .init(statementId: id))) {
        case .noContent:
            return
        case .notFound:
            return  // already gone
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func scanCardStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.CardStatementScanResult {
        // The scan endpoint has its own media-type enum; bridge by raw value
        // rather than assuming the two stay identical.
        guard case .visual(let mediaType) = attachment.kind,
            let scanMediaType = Components.Schemas.CardStatementScanRequest.ImageMediaTypePayload(
                rawValue: mediaType.rawValue)
        else {
            throw CardStatementScanError.unsupportedFile
        }
        let request = Components.Schemas.CardStatementScanRequest(
            imageBase64: attachment.data.base64EncodedString(),
            imageMediaType: scanMediaType
        )
        switch try await client.scanCardStatement(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .unprocessableContent:
            throw APIError.server(422)
        case .serviceUnavailable:
            // No vision model on the box — say so, and let them type it in.
            throw CardStatementScanError.visionModelUnavailable
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    private func patch(id: String, _ request: Components.Schemas.AccountUpdateRequest) async throws {
        switch try await client.updateAccount(
            .init(path: .init(accountId: id), body: .json(request))
        ) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
