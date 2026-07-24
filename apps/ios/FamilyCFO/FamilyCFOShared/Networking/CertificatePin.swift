import CryptoKit
import Foundation

/// Certificate pinning per the mobile spec: the pairing QR carries the
/// SHA-256 of the server's DER certificate and the app pins it — no CA
/// installation dance, self-signed certificates included. Re-pairing
/// rotates the pin.
enum CertificatePin {
    /// Whether a presented DER certificate matches the pinned fingerprint.
    static func matches(certificateDER der: Data, pinnedSHA256Hex pin: String) -> Bool {
        let normalized = pin.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return false }
        return sha256Hex(of: der) == normalized
    }

    static func sha256Hex(of data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

/// ADR 0056: trust-on-first-use for the email-login path, which has no QR to
/// carry a fingerprint. Used for exactly ONE explicit setup request (a health
/// check the user initiates): it accepts the presented server certificate and
/// records its SHA-256 so the user can confirm it — after which every request
/// is pinned to that hash, exactly like a QR pairing. Never used for ongoing
/// traffic.
final class CertificateCaptureDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    private(set) var capturedSHA256Hex: String?

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard
            challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust,
            let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
            let leaf = chain.first
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        capturedSHA256Hex = CertificatePin.sha256Hex(of: SecCertificateCopyData(leaf) as Data)
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

/// URLSession delegate that accepts exactly the pinned server certificate
/// (which is how a self-signed home-server cert becomes trustworthy), and
/// falls back to system TLS evaluation when no pin is configured.
final class PinnedServerTrustDelegate: NSObject, URLSessionDelegate {
    private let pinnedSHA256Hex: String?

    init(pinnedSHA256Hex: String?) {
        self.pinnedSHA256Hex = pinnedSHA256Hex
    }

    func urlSession(
        _ session: URLSession,
        didReceive challenge: URLAuthenticationChallenge,
        completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void
    ) {
        guard
            challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
            let trust = challenge.protectionSpace.serverTrust
        else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        guard let pin = pinnedSHA256Hex else {
            // No pin (the server had no readable cert at pairing time):
            // defer to the system trust store, e.g. a bring-your-own cert
            // signed by a real CA or an external TLS proxy (ADR 0008).
            completionHandler(.performDefaultHandling, nil)
            return
        }
        guard
            let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
            let leaf = chain.first
        else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let der = SecCertificateCopyData(leaf) as Data
        if CertificatePin.matches(certificateDER: der, pinnedSHA256Hex: pin) {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
