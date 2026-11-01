import CryptoKit
import Foundation
import Observation
import UIKit

/// ADR 0079: account-only sign-in into a revocable, device-bound session.
/// First setup confirms server trust; re-entry preserves stored trust.
@MainActor
@Observable
final class LoginViewModel {
    enum Step: Equatable {
        case enterServer
        case checkingServer
        /// The server answered; the user confirms its identity before typing
        /// credentials. `fingerprint` is nil for CA-signed/proxied setups.
        case confirmServer(baseURL: URL, fingerprint: String?)
        case credentials(baseURL: URL, fingerprint: String?)
        case signingIn
        case failed(String)
    }

    private(set) var step: Step = .enterServer

    /// How `signIn` builds its client, and where the flow starts. Production
    /// uses the defaults; tests substitute a stub transport so a real 429 —
    /// `Retry-After` header and all — can be driven through the generated
    /// client, which is the plumbing #92 was actually about.
    typealias ClientBuilder = @Sendable (_ baseURL: URL, _ fingerprint: String?) -> Client

    private let buildClient: ClientBuilder
    private let savePrivateKey: @Sendable (Data) throws -> Void
    private let deletePrivateKey: @Sendable () -> Void
    private let buildRevocationClient: @Sendable (URL, String?, String) -> Client

    init(
        step: Step = .enterServer,
        savePrivateKey: @escaping @Sendable (Data) throws -> Void = {
            try KeychainStore.save($0, account: "device-private-key")
        },
        deletePrivateKey: @escaping @Sendable () -> Void = {
            KeychainStore.delete(account: "device-private-key")
        },
        buildRevocationClient: @escaping @Sendable (URL, String?, String) -> Client = {
            baseURL, fingerprint, token in
            APIClientFactory.makeClient(
                baseURL: baseURL, pinnedCertificateSHA256: fingerprint, token: { token })
        },
        buildClient: @escaping ClientBuilder = { baseURL, fingerprint in
            APIClientFactory.makeClient(baseURL: baseURL, pinnedCertificateSHA256: fingerprint)
        }
    ) {
        self.step = step
        self.buildClient = buildClient
        self.savePrivateKey = savePrivateKey
        self.deletePrivateKey = deletePrivateKey
        self.buildRevocationClient = buildRevocationClient
    }

    var serverAddress: String = ""
    var email: String = ""
    var password: String = ""
    var deviceName: String = UIDevice.current.name

    /// The port the stack serves. compose publishes this and nothing else, so
    /// an address without a port means this one — not 443, which is what a URL
    /// otherwise implies and where nothing is listening (#54).
    static let defaultPort = 8443

    /// "192.168.1.10:8443" → https://…/api/v1. Accepts a bare host, host:port,
    /// or a full URL; https is assumed (the box terminates TLS at nginx).
    ///
    /// A missing port becomes 8443. Someone fronting the box with a reverse
    /// proxy on 443 has to say `:443`, which is the right way round: every
    /// deployment this app supports today serves 8443, so defaulting to 443
    /// fails for everyone in order to suit no one.
    static func normalizedBaseURL(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") { text = "https://" + text }
        guard var components = URLComponents(string: text),
            components.scheme?.lowercased() == "https",
            components.user == nil, components.password == nil,
            components.host?.isEmpty == false
        else { return nil }
        if components.port == nil { components.port = defaultPort }
        components.query = nil
        components.fragment = nil
        var path = components.path
        while path.hasSuffix("/") { path.removeLast() }
        if !path.hasSuffix("/api/v1") { path += "/api/v1" }
        components.path = path
        return components.url
    }

    func checkServer() async {
        guard let baseURL = Self.normalizedBaseURL(serverAddress) else {
            step = .failed(String(localized: "Enter the server address, e.g. 192.168.1.10:8443"))
            return
        }
        step = .checkingServer
        let capture = CertificateCaptureDelegate()
        let session = URLSession(
            configuration: .ephemeral, delegate: capture, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        do {
            let (_, response) = try await session.data(from: baseURL.appending(path: "health"))
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                step = .failed(
                    String(localized: "That address answered, but not like a Family CFO server."))
                return
            }
            step = .confirmServer(baseURL: baseURL, fingerprint: capture.capturedSHA256Hex)
        } catch {
            step = .failed(Self.describeServerCheck(error))
        }
    }

    static func describeServerCheck(_ error: Error) -> String {
        SignInErrorDescriber.describe(error)
    }

    /// No probe and no TOFU on re-entry: use precisely the saved address/pin.
    func restoreServer(_ server: ServerConfig?) {
        guard step == .enterServer, serverAddress.isEmpty, let server,
            server.apiBaseURL.scheme?.lowercased() == "https"
        else { return }
        serverAddress = server.apiBaseURL.absoluteString
        step = .credentials(baseURL: server.apiBaseURL, fingerprint: server.certificateSHA256)
    }

    func confirmServer() {
        guard case .confirmServer(let baseURL, let fingerprint) = step else { return }
        step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
    }

    func startOver() {
        password = ""
        signInError = nil
        step = .enterServer
    }

    func signIn(into model: AppModel) async {
        guard case .credentials(let baseURL, let fingerprint) = step else { return }
        guard baseURL.scheme?.lowercased() == "https",
            baseURL.user == nil, baseURL.password == nil else {
            step = .failed(String(localized: "Use an HTTPS server address without embedded credentials."))
            return
        }
        signInError = nil
        step = .signingIn
        // The private key stays on the device.
        let privateKey = P256.Signing.PrivateKey()
        let client = buildClient(baseURL, fingerprint)
        do {
            let output = try await client.createDeviceSessionWithPassword(
                .init(
                    body: .json(
                        .init(
                            email: email.trimmingCharacters(in: .whitespaces),
                            password: password,
                            deviceName: deviceName.isEmpty ? "iPhone" : deviceName,
                            devicePublicKey: privateKey.publicKey.rawRepresentation
                                .base64EncodedString()
                        )
                    )
                )
            )
            switch output {
            case .created(let response):
                let credential = try response.body.json
                var privateKeySaved = false
                let saved: Bool
                do {
                    try savePrivateKey(privateKey.rawRepresentation)
                    privateKeySaved = true
                    saved = model.completePairing(
                        server: ServerConfig(
                            apiBaseURL: baseURL,
                            certificateSHA256: fingerprint,
                            householdID: credential.householdId ?? "",
                            householdName: credential.householdName ?? "Your household",
                            deviceName: deviceName
                        ),
                        credential: StoredCredential(
                            deviceID: credential.deviceId,
                            accessToken: credential.accessToken,
                            expiresAt: credential.expiresAt,
                            role: credential.role,
                            roleName: credential.roleName,
                            rights: credential.rights
                        )
                    )
                } catch {
                    saved = false
                }
                password = ""
                if !saved {
                    if privateKeySaved { deletePrivateKey() }
                    // The server already issued this session. Never use an old
                    // model credential or change trust while compensating for
                    // a local save failure. Cleanup failure must not hide it.
                    let cleanup = buildRevocationClient(baseURL, fingerprint, credential.accessToken)
                    _ = try? await cleanup.deleteAuthSession(.init())
                    step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
                    signInError = String(localized: "Could not securely save this device’s sign-in. Try signing in again.")
                }
            case .unauthorized:
                password = ""
                step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
                signInError = String(localized: "Wrong email or password.")
            case .tooManyRequests(let response):
                // #92: say the wait the server actually named. The lockout
                // defaults to fifteen minutes; this used to print "wait a
                // minute", so people retried, were refused again, and read
                // the wait as a fault.
                step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
                signInError = RetryAfter.tooManyAttemptsMessage(
                    headerValue: response.headers.retryAfter)
            case .undocumented(let status, _):
                step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
                signInError = String(localized: "The server answered with an unexpected status (\(status)).")
            }
        } catch {
            // Retry keeps the attempted URL and trust. Only the explicit
            // Change server action may restart certificate discovery.
            step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
            signInError = SignInErrorDescriber.describe(error)
        }
    }

    var signInError: String?

    /// "ab12cd34…" — first 8 hex chars, enough for a human to compare.
    static func shortFingerprint(_ fingerprint: String?) -> String {
        guard let fingerprint, fingerprint.count >= 8 else {
            return String(localized: "none (CA-signed)")
        }
        return String(fingerprint.prefix(8)) + "…"
    }
}
