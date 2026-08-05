import Foundation

/// The operator's hosted-household roster (#180, ADR 0025 parity with the
/// dashboard's Households page). System admins only — the server 403s others.
protocol HouseholdsAPI: Sendable {
    func list() async throws -> [Components.Schemas.HostedHousehold]
    func create(
        displayName: String, baseCurrency: String, ownerEmail: String
    ) async throws -> Components.Schemas.HostedHouseholdCreateResponse
    /// #189: irreversible — the server 409s the operator's own household.
    func delete(householdID: String) async throws
}

struct LiveHouseholdsAPI: HouseholdsAPI {
    let client: Client

    func list() async throws -> [Components.Schemas.HostedHousehold] {
        switch try await client.listHostedHouseholds(.init()) {
        case .ok(let response):
            return try response.body.json.households
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func create(
        displayName: String, baseCurrency: String, ownerEmail: String
    ) async throws -> Components.Schemas.HostedHouseholdCreateResponse {
        switch try await client.createHostedHousehold(
            .init(
                body: .json(
                    .init(
                        displayName: displayName,
                        baseCurrency: baseCurrency,
                        ownerEmail: ownerEmail)))
        ) {
        case .created(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict(let error):
            // The server explains (the email already has an account) — verbatim.
            if let message = try? error.body.json.error.message {
                throw APIError.conflict(message)
            }
            throw APIError.server(409)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func delete(householdID: String) async throws {
        switch try await client.deleteHostedHousehold(.init(path: .init(householdId: householdID))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound(let error):
            // "Household not found" — already deleted elsewhere; verbatim.
            if let message = try? error.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(404)
        case .conflict(let error):
            // The server explains (can't delete your own household) — verbatim.
            if let message = try? error.body.json.error.message {
                throw APIError.conflict(message)
            }
            throw APIError.server(409)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
