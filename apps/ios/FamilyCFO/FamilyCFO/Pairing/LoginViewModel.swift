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
            step = .failed(
                String(
                    localized:
                        "Could not reach the server: make sure this phone is on the same network (or tailnet) as your Family CFO box."
                )
            )
        }
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
        let client = APIClientFactory.makeClient(
            baseURL: baseURL, pinnedCertificateSHA256: fingerprint)
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
            case .tooManyRequests:
                step = .credentials(baseURL: baseURL, fingerprint: fingerprint)
                signInError = String(localized: "Too many attempts — wait a minute and try again.")
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
