import Foundation
import HTTPTypes
import OpenAPIRuntime
import OpenAPIURLSession

/// Injects the device credential as a bearer token on every request.
struct BearerAuthMiddleware: ClientMiddleware {
    let token: @Sendable () -> String?

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var request = request
        if let token = token() {
            request.headerFields[.authorization] = "Bearer \(token)"
        }
        return try await next(request, body, baseURL)
    }
}

/// #10 phase 4: tells the API which language to write ITS OWN prose in (error
/// details). Supplied as a closure because the household can change language
/// mid-session and every later request must follow, without rebuilding the
/// client.
struct AcceptLanguageMiddleware: ClientMiddleware {
    let language: @Sendable () -> String?

    func intercept(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String,
        next: @Sendable (HTTPRequest, HTTPBody?, URL) async throws -> (HTTPResponse, HTTPBody?)
    ) async throws -> (HTTPResponse, HTTPBody?) {
        var request = request
        if let language = language(), !language.isEmpty {
            request.headerFields[.acceptLanguage] = language
        }
        return try await next(request, body, baseURL)
    }
}

/// The API serializes datetimes with or without fractional seconds depending
/// on sub-second precision, so decode both.
struct LenientDateTranscoder: DateTranscoder {
    func encode(_ date: Date) throws -> String {
        try ISO8601DateTranscoder().encode(date)
    }

    func decode(_ dateString: String) throws -> Date {
        guard let date = ISO8601DateFormatter.lenientDate(from: dateString) else {
            throw DecodingError.dataCorrupted(
                .init(codingPath: [], debugDescription: "Unparseable date: \(dateString)")
            )
        }
        return date
    }
}

enum APIClientFactory {
    /// Builds a generated client bound to the paired server: certificate
    /// pinned when a fingerprint is known, bearer-authenticated when a
    /// token provider is given (pairing confirmation itself runs without).
    /// With a `devicePrivateKey` provider, a 423 sealed-household answer
    /// triggers ONE local unwrap-and-unlock before the request is replayed
    /// (ADR 0072 Phase 3).
    static func makeClient(
        baseURL: URL,
        pinnedCertificateSHA256: String?,
        token: (@Sendable () -> String?)? = nil,
        devicePrivateKey: (@Sendable () -> Data?)? = nil,
        language: (@Sendable () -> String?)? = nil
    ) -> Client {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 180  // grounded answers can take a while on home hardware
        let session = URLSession(
            configuration: configuration,
            delegate: PinnedServerTrustDelegate(pinnedSHA256Hex: pinnedCertificateSHA256),
            delegateQueue: nil
        )
        var middlewares: [ClientMiddleware] = []
        // Outermost, so its unlock sub-requests flow through the bearer
        // middleware below and authenticate like any other call.
        if let devicePrivateKey {
            middlewares.append(HouseholdUnlockMiddleware(devicePrivateKey: devicePrivateKey))
        }
        if let token {
            middlewares.append(BearerAuthMiddleware(token: token))
        }
        if let language {
            middlewares.append(AcceptLanguageMiddleware(language: language))
        }
        return Client(
            serverURL: baseURL,
            configuration: Configuration(dateTranscoder: LenientDateTranscoder()),
            transport: URLSessionTransport(configuration: .init(session: session)),
            middlewares: middlewares
        )
    }
}
