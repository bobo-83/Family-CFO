import Foundation
import Security
import Testing

@testable import FamilyCFO

/// #86: the app must pin a SELF-SIGNED certificate and must NOT pin one that
/// chains to a trusted root, because the latter rotates on its CA's schedule.
///
/// These tests exercise real chain validation, not a flag. Every case below
/// builds a genuine `SecTrust` from genuine DER certificates and lets Security
/// evaluate it — signature, chain, validity window, SSL hostname policy. The
/// only concession is `SecTrustSetAnchorCertificates`, which nominates the
/// fixture root as an ADDITIONAL trusted anchor for that one trust object; the
/// production code never touches anchors, so on a device the same call
/// consults the real system store (asserted in
/// `fixtureRootIsNotInTheSystemStore`).
///
/// That concession is what makes the rotation case testable: `caLeafB` is a
/// different certificate from `caLeafA` — different key, different serial,
/// different fingerprint — issued by the same root for the same host. It is
/// exactly what the box will present after `tailscale cert` renews, and no
/// assertion here would pass by construction if the trust decision were wrong.
struct ServerTrustDecisionTests {
    private let host = TestCertificates.host
    private let otherHost = TestCertificates.otherHost

    // MARK: - The trust decision itself

    @Test func selfSignedCertificateIsNotSystemTrusted() {
        let trust = TestCertificates.trust(chain: [TestCertificates.selfSignedA], host: host)
        #expect(!CertificatePin.isSystemTrusted(trust, host: host))
    }

    @Test func caSignedCertificateIsSystemTrustedForItsOwnHost() {
        #expect(CertificatePin.isSystemTrusted(TestCertificates.caTrust(), host: host))
    }

    @Test func caSignedCertificateForAnotherHostIsRejected() {
        let leaf = TestCertificates.caLeafOtherHost
        // Validly issued, validly chained — and still not this server.
        #expect(!CertificatePin.isSystemTrusted(TestCertificates.caTrust(leaf: leaf), host: host))
        // The same certificate under its own name proves the rejection above is
        // the hostname check and not a broken fixture.
        #expect(
            CertificatePin.isSystemTrusted(
                TestCertificates.caTrust(leaf: leaf, host: otherHost), host: otherHost))
    }

    @Test func anEmptyHostIsNeverTrusted() {
        #expect(!CertificatePin.isSystemTrusted(TestCertificates.caTrust(), host: "  "))
    }

    @Test func fixtureRootIsNotInTheSystemStore() {
        // Proves the anchor injection above is doing the work, i.e. that the
        // production helper really is asking the system store and these
        // certificates are not trusted by accident.
        let trust = TestCertificates.trust(
            chain: [TestCertificates.caLeafA, TestCertificates.root], host: host)
        #expect(!CertificatePin.isSystemTrusted(trust, host: host))
    }

    // MARK: - Pairing: what gets captured

    @Test func pairingCapturesTheFingerprintOfASelfSignedCertificate() {
        let capture = CertificateCaptureDelegate()
        let disposition = capture.run(
            trust: TestCertificates.trust(chain: [TestCertificates.selfSignedA], host: host),
            host: host)
        #expect(disposition == .useCredential)
        #expect(capture.capturedSHA256Hex == TestCertificates.sha256Hex(TestCertificates.selfSignedA))
    }

    @Test func pairingCapturesNoFingerprintForACASignedCertificate() {
        let capture = CertificateCaptureDelegate()
        let disposition = capture.run(trust: TestCertificates.caTrust(), host: host)
        #expect(disposition == .useCredential)
        #expect(capture.capturedSHA256Hex == nil)
    }

    // MARK: - Ongoing requests: self-signed stays pinned

    @Test func pinnedSelfSignedCertificateIsAccepted() {
        let delegate = PinnedServerTrustDelegate(
            pinnedSHA256Hex: TestCertificates.sha256Hex(TestCertificates.selfSignedA))
        let disposition = delegate.run(
            trust: TestCertificates.trust(chain: [TestCertificates.selfSignedA], host: host),
            host: host)
        #expect(disposition == .useCredential)
    }

    @Test func aDifferentSelfSignedCertificateIsStillRejected() {
        let delegate = PinnedServerTrustDelegate(
            pinnedSHA256Hex: TestCertificates.sha256Hex(TestCertificates.selfSignedA))
        let disposition = delegate.run(
            trust: TestCertificates.trust(chain: [TestCertificates.selfSignedB], host: host),
            host: host)
        #expect(disposition == .cancelAuthenticationChallenge)
    }

    // MARK: - Ongoing requests: CA-signed survives rotation

    @Test func unpinnedCASignedServerDefersToTheSystemStore() {
        let delegate = PinnedServerTrustDelegate(pinnedSHA256Hex: nil)
        #expect(delegate.run(trust: TestCertificates.caTrust(), host: host) == .performDefaultHandling)
    }

    /// The whole point of #86: a device paired to a CA-signed host BEFORE this
    /// change carries a pin for a leaf the CA replaces every ~90 days. The
    /// renewed leaf is a different certificate with a different fingerprint,
    /// and it must not lock the household out.
    @Test func aRotatedCASignedCertificateIsAcceptedDespiteAStalePin() {
        let delegate = PinnedServerTrustDelegate(
            pinnedSHA256Hex: TestCertificates.sha256Hex(TestCertificates.caLeafA))
        let rotated = TestCertificates.caTrust(leaf: TestCertificates.caLeafB)
        #expect(TestCertificates.sha256Hex(TestCertificates.caLeafB)
            != TestCertificates.sha256Hex(TestCertificates.caLeafA))
        #expect(delegate.run(trust: rotated, host: host) == .useCredential)
    }

    /// The stale-pin escape is not a blanket "trust anything CA-signed": the
    /// certificate has to be valid for the host we are talking to.
    @Test func aCASignedCertificateForTheWrongHostIsRejectedDespiteAStalePin() {
        let delegate = PinnedServerTrustDelegate(
            pinnedSHA256Hex: TestCertificates.sha256Hex(TestCertificates.caLeafA))
        let wrong = TestCertificates.caTrust(leaf: TestCertificates.caLeafOtherHost)
        #expect(delegate.run(trust: wrong, host: host) == .cancelAuthenticationChallenge)
    }

    /// …and it does not rescue an untrusted certificate either: a self-signed
    /// impostor presented to a device that pinned a different self-signed
    /// certificate still fails, which is the LAN/WireGuard case unchanged.
    @Test func theStalePinEscapeNeedsARealChain() {
        let delegate = PinnedServerTrustDelegate(
            pinnedSHA256Hex: TestCertificates.sha256Hex(TestCertificates.caLeafA))
        let impostor = TestCertificates.trust(chain: [TestCertificates.selfSignedB], host: host)
        #expect(delegate.run(trust: impostor, host: host) == .cancelAuthenticationChallenge)
    }

    // MARK: - The phone→watch relay

    /// The watch receives the pin as a string over WatchConnectivity, so "no
    /// pin" arrives as "". Treated literally it pinned the watch to a hash
    /// nothing can match and every request failed — on the watch only.
    @Test func anEmptyRelayedPinMeansNoPin() {
        #expect(CertificatePin.normalizedPin("") == nil)
        #expect(CertificatePin.normalizedPin("   ") == nil)
        #expect(CertificatePin.normalizedPin(nil) == nil)
        let delegate = PinnedServerTrustDelegate(pinnedSHA256Hex: "")
        #expect(delegate.run(trust: TestCertificates.caTrust(), host: host) == .performDefaultHandling)
    }
}

// MARK: - Driving a URLSessionDelegate without a network

extension URLSessionDelegate {
    /// Feeds the delegate a server-trust challenge for `trust` and returns the
    /// disposition it chose.
    fileprivate func run(trust: SecTrust, host: String) -> URLSession.AuthChallengeDisposition {
        let challenge = URLAuthenticationChallenge(
            protectionSpace: StubProtectionSpace(trust: trust, host: host),
            proposedCredential: nil,
            previousFailureCount: 0,
            failureResponse: nil,
            error: nil,
            sender: StubChallengeSender()
        )
        var chosen: URLSession.AuthChallengeDisposition = .rejectProtectionSpace
        urlSession?(.shared, didReceive: challenge) { disposition, _ in chosen = disposition }
        return chosen
    }
}

/// `URLProtectionSpace.serverTrust` is read-only, so the only way to hand a
/// delegate a trust of our choosing is to override it.
private final class StubProtectionSpace: URLProtectionSpace, @unchecked Sendable {
    private let trust: SecTrust

    init(trust: SecTrust, host: String) {
        self.trust = trust
        super.init(
            host: host, port: 443, protocol: "https", realm: nil,
            authenticationMethod: NSURLAuthenticationMethodServerTrust)
    }

    required init?(coder: NSCoder) { fatalError("unused") }

    override var serverTrust: SecTrust? { trust }
}

/// Never consulted — `URLAuthenticationChallenge` just requires one.
private final class StubChallengeSender: NSObject, URLAuthenticationChallengeSender {
    func use(_ credential: URLCredential, for challenge: URLAuthenticationChallenge) {}
    func continueWithoutCredential(for challenge: URLAuthenticationChallenge) {}
    func cancel(_ challenge: URLAuthenticationChallenge) {}
}
