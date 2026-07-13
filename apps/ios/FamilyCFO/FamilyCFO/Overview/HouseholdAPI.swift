import Foundation

/// The daily-glance context behind the Overview tab (M88). Read-only: every
/// number here is computed server-side by the deterministic engine, so the
/// phone renders what the dashboard renders and cannot drift from it.
protocol HouseholdAPI: Sendable {
    func context() async throws -> Components.Schemas.HouseholdContext
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
