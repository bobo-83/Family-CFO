import Foundation

/// #29: record a transaction by hand.
///
/// Everything else on the phone reads a ledger the bank feed fills. Two things
/// never reach it — cash, and a statement charge the feed simply never
/// delivered (#25) — and until now the phone could show the hole but not close
/// it. This is the one write that closes it, over the same `POST /transactions`
/// the dashboard has always used, so both surfaces store the same row.
protocol AddTransactionAPI: Sendable {
    /// Every account, unfiltered — the sheet decides which ones money can be
    /// spent from (a mortgage isn't one).
    func accounts() async throws -> [Components.Schemas.Account]
    func categories() async throws -> [Components.Schemas.Category]
    /// `POST /transactions`. The server gates this on `transactions.manage` and
    /// records `transaction.created`, which is UNDOABLE — a mistake here is
    /// reversible from Activity, exactly like the dashboard's.
    func create(_ transaction: NewTransaction) async throws
}

/// One hand-entered transaction, already in the ledger's sign convention.
///
/// The API takes the amount as-is, so the conversion from "an expense of
/// $12.50" to `-1250` happens ONCE, in the view model, and is pinned by a test
/// — never re-derived at the call site.
struct NewTransaction: Equatable {
    var accountID: String
    /// ISO "yyyy-MM-dd": the contract's `occurred_at` is a date, not a moment.
    var occurredOn: String
    /// NEGATIVE for money spent, positive for money arriving.
    var amountMinor: Int64
    /// Must equal the account's own currency — the server refuses anything else.
    var currency: String
    var merchant: String?
    var description: String?
    var categoryID: String?
}

struct LiveAddTransactionAPI: AddTransactionAPI {
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

    func categories() async throws -> [Components.Schemas.Category] {
        switch try await client.listCategories(.init()) {
        case .ok(let response):
            return try response.body.json.categories
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func create(_ transaction: NewTransaction) async throws {
        let request = Components.Schemas.TransactionCreateRequest(
            accountId: transaction.accountID,
            occurredAt: transaction.occurredOn,
            amount: .init(amountMinor: transaction.amountMinor, currency: transaction.currency),
            merchant: transaction.merchant,
            description: transaction.description,
            categoryId: transaction.categoryID
        )
        switch try await client.createTransaction(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            // The account (or category) went away between loading the form and
            // saving it — a reload fixes it, so say the status plainly.
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
