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
    /// Pull fresh data from the linked banks (SimpleFIN) — so a newly-added
    /// account shows up on pull-to-refresh, not only after a manual sync.
    func syncBanks() async throws
    /// Add an account by hand — for holdings a bank feed can't reach (e.g. a
    /// HealthEquity HSA). Creates the account and records its current balance.
    func createManualAccount(
        name: String, type: Components.Schemas.AccountType, currency: String, balanceMinor: Int64
    ) async throws
    /// ADR 0057: read a statement photo/PDF into add-account candidates.
    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.AccountScanResult
}

extension AccountsAPI {
    /// Default so mocks/tests needn't implement it; the live client overrides.
    func scanStatement(
        _ attachment: ChatAttachment
    ) async throws -> Components.Schemas.AccountScanResult {
        throw APIError.server(501)
    }
}

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
