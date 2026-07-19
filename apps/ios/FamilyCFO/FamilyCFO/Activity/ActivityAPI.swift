import Foundation

/// `AuditEvent` carries a stable `id`; conforming drives `ForEach` directly.
extension Components.Schemas.AuditEvent: Identifiable {}

/// The Activity/History surface (M101): every recorded action, newest first, with
/// a durable undo for the reversible ones (unlike the transient undo bar, which
/// disappears after a few seconds).
protocol ActivityAPI: Sendable {
    func events() async throws -> [Components.Schemas.AuditEvent]
    /// Reverse a reversible action; returns the updated (now-reverted) event.
    func undo(eventID: String) async throws -> Components.Schemas.AuditEvent
}

struct LiveActivityAPI: ActivityAPI {
    let client: Client

    func events() async throws -> [Components.Schemas.AuditEvent] {
        switch try await client.listAuditEvents(.init()) {
        case .ok(let response): return try response.body.json.events
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func undo(eventID: String) async throws -> Components.Schemas.AuditEvent {
        switch try await client.undoAuditEvent(.init(path: .init(auditId: eventID))) {
        case .ok(let response): return try response.body.json
        case .badRequest: throw APIError.server(400)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .notFound: throw APIError.server(404)
        case .conflict: throw APIError.server(409)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }
}
