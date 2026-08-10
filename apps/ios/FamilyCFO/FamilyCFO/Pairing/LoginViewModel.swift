import CryptoKit
import Foundation
import Observation
import UIKit

/// ADR 0056: the email-login path onto a box — credentialed pairing. The user
/// types the server address; ONE explicit health request captures the server's
/// certificate fingerprint (trust-on-first-use, the same trust act as scanning
/// the admin's QR); the user confirms it; then `/pairing/login` runs PINNED to
/// that fingerprint and yields an ordinary paired-device credential.
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

    init(
        step: Step = .enterServer,
        buildClient: @escaping ClientBuilder = { baseURL, fingerprint in
            APIClientFactory.makeClient(baseURL: baseURL, pinnedCertificateSHA256: fingerprint)
        }
    ) {
        self.step = step
        self.buildClient = buildClient
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
        guard var components = URLComponents(string: text), components.host?.isEmpty == false
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

    /// Why the health probe failed, in words that point at the right thing.
    ///
    /// This used to discard `error` and always say "could not reach the
    /// server", which sent two separate debugging sessions at the network
    /// while the causes were a missing port and something else entirely. Every
    /// cause produced identical text, so the message was unfalsifiable.
    ///
    /// The trailing code is deliberate. A self-hosted box is operated by the
    /// person reading this, and `NSURLErrorDomain -1202` is the difference
    /// between "your certificate changed" and "nothing is listening" — worth
    /// more than a tidier sentence.
    static func describeServerCheck(_ error: Error) -> String {
        let nsError = error as NSError
        let detail = "\(nsError.domain) \(nsError.code)"
        let explanation: String
        switch (nsError.domain, nsError.code) {
        case (NSURLErrorDomain, NSURLErrorServerCertificateUntrusted),
            (NSURLErrorDomain, NSURLErrorSecureConnectionFailed),
            (NSURLErrorDomain, NSURLErrorServerCertificateHasBadDate),
            (NSURLErrorDomain, NSURLErrorServerCertificateNotYetValid):
            explanation = String(
                localized:
                    "The server answered but its certificate was rejected. If the box's certificate was regenerated, this is expected — unpair and pair again."
            )
        case (NSURLErrorDomain, NSURLErrorCannotConnectToHost):
            explanation = String(
                localized:
                    "Nothing is listening at that address and port. The box serves 8443; check the port."
            )
        case (NSURLErrorDomain, NSURLErrorCannotFindHost):
            explanation = String(
                localized:
                    "That name could not be resolved. If it is a tailnet name, check the VPN is connected."
            )
        case (NSURLErrorDomain, NSURLErrorTimedOut):
            explanation = String(
                localized:
                    "The address is reachable in principle but nothing answered in time — usually a firewall between this phone and the box."
            )
        case (NSURLErrorDomain, NSURLErrorNotConnectedToInternet),
            (NSURLErrorDomain, NSURLErrorNetworkConnectionLost):
            explanation = String(
                localized: "This phone lost its network connection.")
        case (NSURLErrorDomain, NSURLErrorAppTransportSecurityRequiresSecureConnection):
            explanation = String(
                localized:
                    "iOS blocked the connection before it was made (App Transport Security).")
        default:
            explanation = String(
                localized:
                    "Could not reach the server: make sure this phone is on the same network (or tailnet) as your Family CFO box."
            )
        }
        return "\(explanation)\n\n[\(detail): \(nsError.localizedDescription)]"
    }

    func confirmServer() {
        guard case .confirmServer(let baseURL, let fingerprint) = step else { return }
        step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
    }

    func startOver() {
        step = .enterServer
    }

    func signIn(into model: AppModel) async {
        guard case .credentials(let baseURL, let fingerprint) = step else { return }
        step = .signingIn
        // The private key stays on the device, like QR pairing (M83).
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
                try? KeychainStore.save(
                    privateKey.rawRepresentation, account: "device-private-key")
                model.completePairing(
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
                step = .failed(
                    String(localized: "The server answered with an unexpected status (\(status))."))
            }
        } catch {
            step = .failed(
                PairingViewModel.describeTransportFailure(error, pinned: fingerprint != nil))
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
