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
    static func makeClient(
        baseURL: URL,
        pinnedCertificateSHA256: String?,
        token: (@Sendable () -> String?)? = nil
    ) -> Client {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 180  // grounded answers can take a while on home hardware
        let session = URLSession(
            configuration: configuration,
            delegate: PinnedServerTrustDelegate(pinnedSHA256Hex: pinnedCertificateSHA256),
            delegateQueue: nil
        )
        var middlewares: [ClientMiddleware] = []
        if let token {
            middlewares.append(BearerAuthMiddleware(token: token))
        }
        return Client(
            serverURL: baseURL,
            configuration: Configuration(dateTranscoder: LenientDateTranscoder()),
            transport: URLSessionTransport(configuration: .init(session: session)),
            middlewares: middlewares
        )
    }
}
