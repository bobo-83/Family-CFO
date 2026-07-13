import CryptoKit
import Foundation
import Testing

@testable import FamilyCFO

struct CertificatePinTests {
    private let der = Data("der-shaped-bytes-for-the-pin-test".utf8)

    private var derSHA256Hex: String {
        SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }

    @Test func matchesTheServerCertificateFingerprint() {
        #expect(CertificatePin.matches(certificateDER: der, pinnedSHA256Hex: derSHA256Hex))
    }

    @Test func matchingIsCaseInsensitiveAndTrimmed() {
        #expect(
            CertificatePin.matches(
                certificateDER: der,
                pinnedSHA256Hex: " " + derSHA256Hex.uppercased() + "\n"
            ))
    }

    @Test func rejectsADifferentCertificate() {
        #expect(
            !CertificatePin.matches(
                certificateDER: Data("some-other-certificate".utf8),
                pinnedSHA256Hex: derSHA256Hex
            ))
    }

    @Test func rejectsAnEmptyPin() {
        #expect(!CertificatePin.matches(certificateDER: der, pinnedSHA256Hex: "  "))
    }

    @Test func hexDigestMatchesTheServersImplementation() {
        // Mirrors apps/api test: SHA-256 over the DER bytes, lowercase hex.
        #expect(CertificatePin.sha256Hex(of: Data("qr-payload-cert".utf8)).count == 64)
    }
}
