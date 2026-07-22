import Foundation
import OpenAPIRuntime

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
    /// The advisor itself reported a failure (streamed `error` event) — the
    /// message is already user-appropriate.
    case advisor(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "This device's pairing is no longer valid. Re-pair from the dashboard's Devices page."
        case .server(let status):
            return "The server answered with an unexpected status (\(status))."
        case .advisor(let message):
            return message
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
    /// Streamed variant (ADR 0061): `onProgress` receives live one-line
    /// narration while the grounded loop works ("Solving for your retirement
    /// age"); the returned response is the same guardrail-validated answer
    /// the plain endpoint delivers. The default implementation falls back to
    /// the plain send, so mocks and older servers keep working.
    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?,
        onProgress: @escaping @Sendable (String) -> Void
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

extension AdvisorAPI {
    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?,
        onProgress: @escaping @Sendable (String) -> Void
    ) async throws -> Components.Schemas.ChatResponse {
        try await sendMessage(message, conversationID: conversationID, attachment: attachment)
    }
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

    private func chatRequest(
        _ message: String, conversationID: String?, attachment: ChatAttachment?
    ) -> Components.Schemas.ChatRequest {
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
        return request
    }

    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?
    ) async throws -> Components.Schemas.ChatResponse {
        let request = chatRequest(message, conversationID: conversationID, attachment: attachment)
        switch try await client.createChatMessage(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func sendMessage(
        _ message: String,
        conversationID: String?,
        attachment: ChatAttachment?,
        onProgress: @escaping @Sendable (String) -> Void
    ) async throws -> Components.Schemas.ChatResponse {
        let request = chatRequest(message, conversationID: conversationID, attachment: attachment)
        switch try await client.createChatMessageStream(.init(body: .json(request))) {
        case .ok(let response):
            return try await Self.consumeEventStream(
                try response.body.textEventStream, onProgress: onProgress
            )
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            if status == 404 {
                // A box that predates ADR 0061 — plain send still works.
                return try await sendMessage(
                    message, conversationID: conversationID, attachment: attachment)
            }
            throw APIError.server(status)
        }
    }

    /// Parse SSE frames: `data: <ChatStreamEvent JSON>` separated by blank
    /// lines; comment lines (": ping") are keepalives and skipped. Connection
    /// failures mid-stream throw URLErrors, which the callers'
    /// SavedAnswerRecovery already knows how to survive.
    static func consumeEventStream(
        _ body: OpenAPIRuntime.HTTPBody,
        onProgress: @escaping @Sendable (String) -> Void
    ) async throws -> Components.Schemas.ChatResponse {
        let decoder = JSONDecoder()
        var buffer = [UInt8]()
        let separator: [UInt8] = [0x0A, 0x0A]  // "\n\n"
        for try await chunk in body {
            buffer.append(contentsOf: chunk)
            while let range = buffer.firstRange(of: separator) {
                let frame = Array(buffer[..<range.lowerBound])
                buffer.removeSubrange(..<range.upperBound)
                guard let answer = try Self.handleFrame(frame, decoder: decoder, onProgress: onProgress)
                else { continue }
                return answer
            }
        }
        // EOF: a final frame may sit in the buffer without its terminating
        // blank line — parse it before deciding the stream was truncated.
        if let answer = try Self.handleFrame(buffer, decoder: decoder, onProgress: onProgress) {
            return answer
        }
        // Stream closed without an answer event: the server always ends with
        // answer or error, so this is a truncated stream — surface it as a
        // dropped connection so recovery kicks in.
        throw URLError(.networkConnectionLost)
    }

    private static func handleFrame(
        _ frame: [UInt8],
        decoder: JSONDecoder,
        onProgress: @escaping @Sendable (String) -> Void
    ) throws -> Components.Schemas.ChatResponse? {
        for line in frame.split(separator: 0x0A) {
            let prefix = Array("data: ".utf8)
            guard line.starts(with: prefix) else { continue }  // ": ping" keepalive
            let payload = Data(line.dropFirst(prefix.count))
            let event = try decoder.decode(Components.Schemas.ChatStreamEvent.self, from: payload)
            switch event._type {
            case .progress:
                if let detail = event.detail {
                    onProgress(detail)
                }
            case .answer:
                guard let response = event.response else {
                    throw APIError.server(502)
                }
                return response
            case .error:
                throw APIError.advisor(
                    event.message ?? "The advisor hit an unexpected error.")
            }
        }
        return nil
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
