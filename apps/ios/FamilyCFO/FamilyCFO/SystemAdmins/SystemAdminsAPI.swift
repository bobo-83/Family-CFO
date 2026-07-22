import Foundation

/// Box-level operator roster (ADR 0065): who may swap the AI model and manage
/// backups for the WHOLE box. Parity with the dashboard's Users page section.
protocol SystemAdminsAPI: Sendable {
    func list() async throws -> [Components.Schemas.SystemAdmin]
    func grant(email: String) async throws -> Components.Schemas.SystemAdmin
    func revoke(userID: String) async throws
}

struct LiveSystemAdminsAPI: SystemAdminsAPI {
    let client: Client

    func list() async throws -> [Components.Schemas.SystemAdmin] {
        switch try await client.listSystemAdmins(.init()) {
        case .ok(let response):
            return try response.body.json.admins
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func grant(email: String) async throws -> Components.Schemas.SystemAdmin {
        switch try await client.grantSystemAdmin(.init(body: .json(.init(email: email)))) {
        case .created(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.advisor("No user with that email — invite them to the household first.")
        case .conflict:
            throw APIError.advisor("They are already a system administrator.")
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func revoke(userID: String) async throws {
        switch try await client.revokeSystemAdmin(.init(path: .init(userId: userID))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            // Already off the roster — the caller wanted them off.
            return
        case .conflict:
            throw APIError.advisor("The box must keep at least one system administrator.")
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
