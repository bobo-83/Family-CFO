import Foundation
import HTTPTypes
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

struct AdvisorStreamFailure: Error {
    let underlyingError: Error
    let recoveryDeadline: ContinuousClock.Instant?
}

extension AdvisorStreamFailure {
    /// The wrapper chain — this type and the generated client's `ClientError`
    /// both wrap causes — flattened outermost-first, ending at the root
    /// transport error. One walk shared by `SavedAnswerRecovery` and
    /// `AdvisorErrorDescriber`, so a future wrapper type cannot be unwrapped
    /// in one place and forgotten in the other (the drift that once made the
    /// describer mis-describe wrapped transport errors, issue #124).
    static func unwrapChain(of error: Error) -> [Error] {
        var chain = [error]
        while true {
            switch chain[chain.count - 1] {
            case let failure as AdvisorStreamFailure:
                chain.append(failure.underlyingError)
            case let clientError as ClientError:
                chain.append(clientError.underlyingError)
            default:
                return chain
            }
        }
    }

    /// The stream-failure wrapper inside `error`, if the failure came from a
    /// consumed stream (it carries the server-advertised recovery deadline).
    static func find(in error: Error) -> AdvisorStreamFailure? {
        for element in unwrapChain(of: error) {
            if let failure = element as? AdvisorStreamFailure { return failure }
        }
        return nil
    }

    /// The root transport error beneath every wrapper.
    static func rootTransportError(of error: Error) -> Error {
        unwrapChain(of: error).last ?? error
    }
}

/// M95: a successful streamed chat response advertises the remaining lifetime
/// of the server's bounded turn in `X-Advisor-Recovery-Horizon-Seconds`. The
/// frozen oldest compatible contract (ADR 0074) generates no accessor for
/// that header, so `AdvisorRecoveryHorizonMiddleware` captures it from the
/// raw response fields into this box — converted at receipt into the local
/// monotonic deadline `SavedAnswerRecovery` polls against (ADR 0061).
final class AdvisorRecoveryHorizon: @unchecked Sendable {
    private let lock = NSLock()
    private var deadline: ContinuousClock.Instant?

    /// Called when a stream request starts: a failure before response headers
    /// arrive must fall back to the compatibility horizon, not inherit the
    /// previous turn's deadline.
    func clear() {
        lock.withLock { deadline = nil }
    }

    func record(secondsRemaining: Int) {
        let instant = ContinuousClock.now.advanced(by: .seconds(secondsRemaining))
        lock.withLock { deadline = instant }
    }

    /// The most recent stream's recovery deadline, or nil when the server
    /// sent none (an older box, or the request died before headers).
    func currentDeadline() -> ContinuousClock.Instant? {
        lock.withLock { deadline }
    }
}

/// Reads the recovery-horizon header off the streamed chat response without
/// touching the generated accessor, keeping this shared source compilable
/// against every supported contract (`scripts/check-client-compatibility.sh`).
struct AdvisorRecoveryHorizonMiddleware: ClientMiddleware {
    static let headerName = HTTPField.Name("X-Advisor-Recovery-Horizon-Seconds")!

    let horizon: AdvisorRecoveryHorizon

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        guard operationID == Operations.CreateChatMessageStream.id else {
            return try await next(request, body, baseURL)
        }
        horizon.clear()
        let (response, responseBody) = try await next(request, body, baseURL)
        if let value = response.headerFields[Self.headerName],
            let seconds = Int(value), seconds > 0
        {
            horizon.record(secondsRemaining: seconds)
        }
        return (response, responseBody)
    }
}

enum APIError: Error, LocalizedError, Equatable {
    case unauthorized
    case server(Int)
    /// The advisor itself reported a failure (streamed `error` event) — the
    /// message is already user-appropriate.
    case advisor(String)
    /// HTTP 409 with the server's explanation of why it refused (e.g. revoking
    /// the device the session runs on) — shown verbatim.
    case conflict(String)

    var errorDescription: String? {
        switch self {
        case .unauthorized:
            return "This device's pairing is no longer valid. Re-pair from the dashboard's Devices page."
        case .server(let status):
            // 423 (ADR 0072 Phase 3): sealed household, no live key session,
            // and the device auto-unlock couldn't open one — the server's
            // household_locked message, so every screen says what to do.
            if status == 423 {
                return "This household's data is sealed and currently locked. Sign in again to unlock it."
            }
            return "The server answered with an unexpected status (\(status))."
        case .advisor(let message):
            return message
        case .conflict(let message):
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
    /// Present when the client stack includes `AdvisorRecoveryHorizonMiddleware`
    /// (M95); nil keeps mocks and older wiring on the compatibility fallback.
    var recoveryHorizon: AdvisorRecoveryHorizon? = nil

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
        let output = try await client.createChatMessage(.init(body: .json(request)))
        if case .ok(let response) = output {
            return try response.body.json
        }
        if case .unauthorized = output {
            throw APIError.unauthorized
        }
        if case .undocumented(let status, _) = output {
            throw APIError.server(status)
        }
        // Newer contracts document 504, while the oldest compatible contract
        // reports it as undocumented. Pattern checks keep this shared source
        // compilable against both generated enum shapes.
        throw APIError.server(504)
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
            do {
                return try await Self.consumeEventStream(
                    try response.body.textEventStream, onProgress: onProgress
                )
            } catch let error as APIError {
                throw error
            } catch {
                // The server-advertised horizon — already converted to a local
                // monotonic deadline by AdvisorRecoveryHorizonMiddleware —
                // rides the failure, so SavedAnswerRecovery polls exactly as
                // long as the box can still save the answer (M95), including
                // when the operator raised FAMILY_CFO_CHAT_TURN_TIMEOUT_SECONDS
                // beyond the client's 10-minute compatibility fallback. nil
                // (older server, or no middleware) means that fallback.
                throw AdvisorStreamFailure(
                    underlyingError: error,
                    recoveryDeadline: recoveryHorizon?.currentDeadline())
            }
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
