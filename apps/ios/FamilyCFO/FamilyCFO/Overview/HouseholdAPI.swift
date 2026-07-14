import Foundation

/// The daily-glance context behind the Overview tab (M88). Read-only: every
/// number here is computed server-side by the deterministic engine, so the
/// phone renders what the dashboard renders and cannot drift from it.
protocol HouseholdAPI: Sendable {
    func context() async throws -> Components.Schemas.HouseholdContext
    /// All transactions — the caller filters (e.g. to a category + month) to drill
    /// into a Spending-by-category total (M94). The list endpoint takes no filter.
    func transactions() async throws -> [Components.Schemas.Transaction]
}

struct LiveHouseholdAPI: HouseholdAPI {
    let client: Client

    func context() async throws -> Components.Schemas.HouseholdContext {
        switch try await client.getHouseholdContext(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func transactions() async throws -> [Components.Schemas.Transaction] {
        switch try await client.listTransactions(.init()) {
        case .ok(let response):
            return try response.body.json.transactions
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}

/// Selects and totals the transactions behind one category's monthly spend
/// (M94), matching the server's card: outflows only, in the given ISO month.
/// Pure, so the filtering/ordering is testable.
enum CategorySpendingDetail {
    static func items(
        in transactions: [Components.Schemas.Transaction],
        categoryID: String,
        month: String
    ) -> [Components.Schemas.Transaction] {
        transactions
            .filter { txn in
                txn.categoryId == categoryID
                    && txn.amount.amountMinor < 0  // outflow, as the card sums
                    && txn.occurredAt.hasPrefix(month)
            }
            .sorted { $0.amount.amountMinor < $1.amount.amountMinor }  // biggest spend first
    }

    static func total(_ items: [Components.Schemas.Transaction], currency: String) -> Components.Schemas.Money {
        .init(amountMinor: items.reduce(0) { $0 - $1.amount.amountMinor }, currency: currency)
    }
}

extension Components.Schemas.Money {
    /// Minor units are the contract's storage (M2) — never format them raw.
    var decimalValue: Decimal {
        Decimal(amountMinor) / 100
    }

    var formatted: String {
        decimalValue.formatted(.currency(code: currency).precision(.fractionLength(0)))
    }

    /// Two-decimal form, for amounts where the cents carry meaning (a single
    /// bill) rather than adding noise (net worth).
    var formattedExact: String {
        decimalValue.formatted(.currency(code: currency))
    }
}
