import CryptoKit
import Foundation
import Security

/// Certificate pinning per the mobile spec: the pairing QR carries the
/// SHA-256 of the server's DER certificate and the app pins it — no CA
/// installation dance, self-signed certificates included. Re-pairing
/// rotates the pin.
///
/// #86: pinning is only right for a SELF-SIGNED certificate, where the
/// fingerprint is the whole of the trust. A certificate that chains to a root
/// the system already trusts is authenticated by its CA, and its leaf rotates
/// on the CA's schedule (~90 days for an ACME-issued one) — pinning such a
/// certificate turns every renewal into a silent lockout. So: pin what the
/// system cannot vouch for, and let the system vouch for the rest.
enum CertificatePin {
    /// Whether a presented DER certificate matches the pinned fingerprint.
    static func matches(certificateDER der: Data, pinnedSHA256Hex pin: String) -> Bool {
        let normalized = normalizedPin(pin)
        guard let normalized else { return false }
        return sha256Hex(of: der) == normalized
    }

    /// A pin that is only whitespace is no pin at all. Relays flatten nil to
    /// "" (the phone→watch application context carries strings only), and a
    /// blank string must not read as "pinned to nothing", which rejects every
    /// certificate including the correct one.
    static func normalizedPin(_ pin: String?) -> String? {
        guard let pin else { return nil }
        let normalized = pin.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        return normalized.isEmpty ? nil : normalized
    }

    static func sha256Hex(of data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Whether `trust` chains to a root the system already trusts AND is valid
    /// for `host` — asked of the platform, which is the thing built to answer
    /// it. Deliberately not "does the issuer name look like a public CA": an
    /// issuer name is free text that any self-signed certificate can copy.
    ///
    /// The standard TLS server policy is set explicitly rather than inherited
    /// from whatever handed us the trust, so the hostname is always checked
    /// against the host we believe we are talking to.
    static func isSystemTrusted(_ trust: SecTrust, host: String) -> Bool {
        let host = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !host.isEmpty else { return false }
        let policy = SecPolicyCreateSSL(true, host as CFString)
        guard SecTrustSetPolicies(trust, policy) == errSecSuccess else { return false }
        return SecTrustEvaluateWithError(trust, nil)
    }

    /// The presented leaf's DER bytes, or nil when the chain is unreadable.
    static func leafDER(of trust: SecTrust) -> Data? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
            let leaf = chain.first
        else { return nil }
        return SecCertificateCopyData(leaf) as Data
    }
}

/// ADR 0056: trust-on-first-use for the email-login path, which has no QR to
/// carry a fingerprint. Used for exactly ONE explicit setup request (a health
/// check the user initiates): it accepts the presented server certificate and
/// records its SHA-256 so the user can confirm it — after which every request
/// is pinned to that hash, exactly like a QR pairing. Never used for ongoing
/// traffic.
///
/// #86: a certificate the system trusts for this host is NOT captured.
/// Recording its fingerprint would pin a leaf that its CA rotates out from
/// under us, and the resulting lockout is silent and dated. `capturedSHA256Hex`
/// staying nil is the signal to the rest of the app that this server is
/// validated by the system trust store instead, which is what the pairing
/// screens already display and what `ServerConfig.certificateSHA256` being
/// optional already stores.
final class CertificateCaptureDelegate: NSObject, URLSessionDelegate, @unchecked Sendable {
    private(set) var capturedSHA256Hex: String?

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
        if CertificatePin.isSystemTrusted(trust, host: challenge.protectionSpace.host) {
            capturedSHA256Hex = nil
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        guard let der = CertificatePin.leafDER(of: trust) else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        // Self-signed: the fingerprint IS the trust, so capture it and let the
        // user confirm it. Accepting the certificate here is the trust-on-
        // first-use act ADR 0056 describes, and it stops at this one request.
        capturedSHA256Hex = CertificatePin.sha256Hex(of: der)
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}

/// URLSession delegate that accepts exactly the pinned server certificate
/// (which is how a self-signed home-server cert becomes trustworthy), and
/// falls back to system TLS evaluation when no pin is configured.
final class PinnedServerTrustDelegate: NSObject, URLSessionDelegate {
    private let pinnedSHA256Hex: String?

    init(pinnedSHA256Hex: String?) {
        self.pinnedSHA256Hex = CertificatePin.normalizedPin(pinnedSHA256Hex)
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
            // No pin: either the server is CA-signed (#86 — nothing was
            // captured at pairing, on purpose) or it had no readable cert at
            // pairing time. Either way defer to the system trust store, which
            // also covers an external TLS proxy (ADR 0008).
            completionHandler(.performDefaultHandling, nil)
            return
        }
        guard let der = CertificatePin.leafDER(of: trust) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        if CertificatePin.matches(certificateDER: der, pinnedSHA256Hex: pin) {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        // The pin missed. Before this device stopped pinning CA-signed
        // certificates (#86), pairing over a name with an ACME certificate
        // recorded a fingerprint for a leaf that its CA replaces roughly every
        // 90 days — so a stale pin is exactly what a rotated-but-legitimate
        // server looks like. Rather than making those households re-pair on a
        // day they cannot predict, drop the stale pin in favour of the system
        // trust store, but only when the system genuinely vouches for THIS
        // host.
        //
        // This cannot weaken the self-signed case: the escape needs a
        // publicly-trusted certificate naming the exact host in the stored
        // address, and no public CA issues for the private IPs (LAN,
        // WireGuard) or the dotless short names those pairings use. Where a
        // public CA *can* issue for the name, the certificate we would be
        // pinning is one it is already free to replace.
        if CertificatePin.isSystemTrusted(trust, host: challenge.protectionSpace.host) {
            completionHandler(.useCredential, URLCredential(trust: trust))
            return
        }
        completionHandler(.cancelAuthenticationChallenge, nil)
    }
}
