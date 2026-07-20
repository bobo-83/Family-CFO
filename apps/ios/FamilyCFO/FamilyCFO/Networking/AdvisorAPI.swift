import Foundation

/// Attachment ready to ride along on a chat message: raw bytes plus how the
/// contract carries them; base64 encoding happens at send time.
///
/// The two kinds travel on *different* request fields and through different
/// server pipelines, so the distinction is load-bearing rather than cosmetic:
/// visuals (images, PDFs — M84) are rasterized for the vision describer, while
/// data files (CSV / spreadsheet / text — M85) are parsed into a bounded
/// grounded preview.
struct ChatAttachment: Equatable {
    enum Kind: Equatable {
        /// `image_base64` + `image_media_type` → vision path (M84).
        case visual(Components.Schemas.ChatRequest.ImageMediaTypePayload)
        /// `data_file_base64` + `data_file_name` → data-file preview (M85).
        case dataFile
    }

    let data: Data
    let kind: Kind
    let displayName: String

    var iconName: String {
        switch kind {
        case .visual(.applicationPdf): return "doc.richtext"
        case .visual: return "photo"
        case .dataFile: return "tablecells"
        }
    }
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
    func deleteConversation(id: String) async throws
    /// ADR 0044: rate an advisor answer 👍/👎, with an optional note the study
    /// job learns from.
    func submitFeedback(
        recommendationId: String,
        rating: Components.Schemas.AdvisorFeedbackRequest.RatingPayload,
        note: String?
    ) async throws
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

    /// Deletes the thread and its messages, server-side and irreversibly — the
    /// same endpoint the dashboard's chat page uses.
    func deleteConversation(id: String) async throws {
        switch try await client.deleteConversation(.init(path: .init(conversationId: id))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            // Already gone — the caller wanted it gone, so this is a success.
            return
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?
    ) async throws -> Components.Schemas.ChatResponse {
        var request = Components.Schemas.ChatRequest(
            conversationId: conversationID,
            message: message
        )
        if let attachment {
            switch attachment.kind {
            case .visual(let mediaType):
                request.imageBase64 = attachment.data.base64EncodedString()
                request.imageMediaType = mediaType
            case .dataFile:
                request.dataFileBase64 = attachment.data.base64EncodedString()
                // The server detects CSV vs XLSX vs text from the extension,
                // so the filename is part of the payload, not decoration.
                request.dataFileName = attachment.displayName
            }
        }
        switch try await client.createChatMessage(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func submitFeedback(
        recommendationId: String,
        rating: Components.Schemas.AdvisorFeedbackRequest.RatingPayload,
        note: String?
    ) async throws {
        let body = Components.Schemas.AdvisorFeedbackRequest(
            recommendationId: recommendationId, rating: rating, note: note
        )
        switch try await client.submitAdvisorFeedback(.init(body: .json(body))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
