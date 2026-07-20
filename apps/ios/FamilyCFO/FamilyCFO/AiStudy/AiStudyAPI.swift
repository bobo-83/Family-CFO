import Foundation

/// ADR 0040: while the box is idle the advisor studies the transaction history
/// one month at a time and remembers the patterns. This surface is read-only —
/// the study job runs on the server; the app just shows coverage and what was
/// learned.
protocol AiStudyAPI: Sendable {
    func status() async throws -> Components.Schemas.AiStudyStatus
}

struct LiveAiStudyAPI: AiStudyAPI {
    let client: Client

    func status() async throws -> Components.Schemas.AiStudyStatus {
        switch try await client.getAiStudyStatus(.init()) {
        case .ok(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }
}
