import Foundation

/// Attachment ready to ride along on a chat message (M84): raw bytes plus
/// the contract media type; base64 encoding happens at send time.
struct ChatAttachment: Equatable {
    let data: Data
    let mediaType: Components.Schemas.ChatRequest.ImageMediaTypePayload
    let displayName: String
}

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "This device's pairing is no longer valid. Re-pair from the dashboard's Devices page."
        case .server(let status):
            return "The server answered with an unexpected status (\(status))."
        }
    }
}

/// The narrow slice of the generated client the app's view models consume.
/// Kept small so tests can substitute a mock without implementing the whole
/// generated `APIProtocol`.
protocol AdvisorAPI: Sendable {
    func listConversations() async throws -> [Components.Schemas.Conversation]
    func conversation(id: String) async throws -> Components.Schemas.ConversationDetail
    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?
    ) async throws -> Components.Schemas.ChatResponse
}

/// Production implementation backed by the generated OpenAPI client.
struct LiveAdvisorAPI: AdvisorAPI {
    let client: Client

    func listConversations() async throws -> [Components.Schemas.Conversation] {
        switch try await client.listConversations(.init()) {
        case .ok(let response):
            return try response.body.json.conversations
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func conversation(id: String) async throws -> Components.Schemas.ConversationDetail {
        switch try await client.getConversation(.init(path: .init(conversationId: id))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?
    ) async throws -> Components.Schemas.ChatResponse {
        let request = Components.Schemas.ChatRequest(
            conversationId: conversationID,
            message: message,
            imageBase64: attachment.map { $0.data.base64EncodedString() },
            imageMediaType: attachment?.mediaType
        )
        switch try await client.createChatMessage(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
