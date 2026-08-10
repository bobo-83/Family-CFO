import Foundation
import Security

@testable import FamilyCFO

/// Real X.509 fixtures for the trust tests (#86). Generated once with openssl
/// and embedded, because Security needs genuine DER — a made-up byte string
/// cannot be chain-validated, and a test that skipped validation would prove
/// nothing about the bug this guards.
///
/// The cast, all P-256/SHA-256:
///
/// - `root` — a CA certificate that stands in for a public root. Nominated as
///   an anchor per trust object; never installed anywhere.
/// - `caLeafA` / `caLeafB` — two DIFFERENT certificates for the same host,
///   issued by `root`. B is A after renewal: different key, different serial,
///   different fingerprint. This pair is the rotation the box will perform.
/// - `caLeafOtherHost` — issued by `root`, but for another name.
/// - `selfSignedA` / `selfSignedB` — two unrelated self-signed certificates
///   for the host, standing in for the box's own certificate and for an
///   impostor's.
///
/// Hostnames are `.invalid` placeholders (ADR 0030). Evaluation runs at a
/// fixed `verifyDate` inside every fixture's validity window so these tests do
/// not start failing on a calendar date.
enum TestCertificates {
    static let host = "server.example.invalid"
    static let otherHost = "other.example.invalid"

    /// 2027-01-01T00:00:00Z.
    static let verifyDate = Date(timeIntervalSince1970: 1_798_761_600)

    static func sha256Hex(_ certificate: SecCertificate) -> String {
        CertificatePin.sha256Hex(of: SecCertificateCopyData(certificate) as Data)
    }

    /// A trust object over `chain`, evaluated at `verifyDate`. `anchors` are
    /// nominated IN ADDITION to the system store, so passing none means the
    /// real system store decides.
    static func trust(
        chain: [SecCertificate], anchors: [SecCertificate] = [], host: String
    ) -> SecTrust {
        var trust: SecTrust?
        let status = SecTrustCreateWithCertificates(
            chain as CFArray, SecPolicyCreateSSL(true, host as CFString), &trust)
        guard status == errSecSuccess, let trust else {
            fatalError("could not build a trust object for the fixtures: \(status)")
        }
        if !anchors.isEmpty {
            SecTrustSetAnchorCertificates(trust, anchors as CFArray)
        }
        SecTrustSetVerifyDate(trust, verifyDate as CFDate)
        return trust
    }

    /// What a CA-signed box presents: leaf plus issuer, with the fixture root
    /// standing in for a root the device already trusts.
    static func caTrust(leaf: SecCertificate? = nil, host: String = TestCertificates.host)
        -> SecTrust
    {
        trust(chain: [leaf ?? caLeafA, root], anchors: [root], host: host)
    }

    static let root = decode(
        """
        MIIBeTCCASCgAwIBAgIJAMPMK3j3CUCqMAoGCCqGSM49BAMCMB8xHTAbBgNVBAMMFEZhbWlseSBD\
        Rk8gVGVzdCBSb290MB4XDTI2MDgxMDIwMjU0NloXDTM2MDgwNzIwMjU0NlowHzEdMBsGA1UEAwwU\
        RmFtaWx5IENGTyBUZXN0IFJvb3QwWTATBgcqhkjOPQIBBggqhkjOPQMBBwNCAAS2O5BTZb0pk2nY\
        9G6hPiDwiqTNiN5yrLW/Cc7nSbiNw37UA+WXgeyQmDUSmAdfmcS3gvZIL3I2BjVco1bRfTUJo0Uw\
        QzASBgNVHRMBAf8ECDAGAQH/AgEAMA4GA1UdDwEB/wQEAwIBBjAdBgNVHQ4EFgQUQeZq+I1wEtPB\
        RoGim4R8gBIY2lwwCgYIKoZIzj0EAwIDRwAwRAIgJ/fJPoXS/q8kDRHIxiu/iYLHD0P53GAJRQef\
        AnOWA6YCICAS7lOb+FSq8UBIvImVPCbQdp9eE94UsDewm3BHMrnB
        """)

    static let caLeafA = decode(
        """
        MIIB0jCCAXegAwIBAgIJAIprrVRX9+LnMAoGCCqGSM49BAMCMB8xHTAbBgNVBAMMFEZhbWlseSBD\
        Rk8gVGVzdCBSb290MB4XDTI2MDgxMDIwMjU0NloXDTI3MDkxMDIwMjU0NlowITEfMB0GA1UEAwwW\
        c2VydmVyLmV4YW1wbGUuaW52YWxpZDBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABDm9F57PPb32\
        NXggZgqWbLnsRckC3UhiC7AWyVPaiDSeZnHlx7mYAQTK/Z14qQ4W6ppSa70vnquZMcbXXLv5ixej\
        gZkwgZYwDAYDVR0TAQH/BAIwADAOBgNVHQ8BAf8EBAMCBaAwEwYDVR0lBAwwCgYIKwYBBQUHAwEw\
        IQYDVR0RBBowGIIWc2VydmVyLmV4YW1wbGUuaW52YWxpZDAdBgNVHQ4EFgQUzTlTSoZ94lEltz4k\
        IarxBGXgJXUwHwYDVR0jBBgwFoAUQeZq+I1wEtPBRoGim4R8gBIY2lwwCgYIKoZIzj0EAwIDSQAw\
        RgIhAJE9FeEtH75WycOuKiqKCHzhHmgFJSguDNd8O6sIAANvAiEAmsC4owfi4qp0tccqyOAGSc2N\
        uhU8U8JWRhpwZdWAIQ0=
        """)

    static let caLeafB = decode(
        """
        MIIB0jCCAXegAwIBAgIJAIprrVRX9+LoMAoGCCqGSM49BAMCMB8xHTAbBgNVBAMMFEZhbWlseSBD\
        Rk8gVGVzdCBSb290MB4XDTI2MDgxMDIwMjU0NloXDTI3MDkxMDIwMjU0NlowITEfMB0GA1UEAwwW\
        c2VydmVyLmV4YW1wbGUuaW52YWxpZDBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABIkoyAoAukAZ\
        3ZWXvyiGUlSjOrHIkAN6khpbViQKT+bAl250ZV8JQmNqIOtnBBppEzzUbgfKI6I34RdR7fwQ5sSj\
        gZkwgZYwDAYDVR0TAQH/BAIwADAOBgNVHQ8BAf8EBAMCBaAwEwYDVR0lBAwwCgYIKwYBBQUHAwEw\
        IQYDVR0RBBowGIIWc2VydmVyLmV4YW1wbGUuaW52YWxpZDAdBgNVHQ4EFgQUMY5XR6fnlcgprt99\
        woftHMS3MeYwHwYDVR0jBBgwFoAUQeZq+I1wEtPBRoGim4R8gBIY2lwwCgYIKoZIzj0EAwIDSQAw\
        RgIhAKjle3MMxenaXcJ/yJm5nVzLua1F91LuVNMp+FVwkbO/AiEAupCEgDUjbqU45riwWME7MbwO\
        tiWCI54PSdFbku7gOLE=
        """)

    static let caLeafOtherHost = decode(
        """
        MIIBzzCCAXWgAwIBAgIJAIprrVRX9+LpMAoGCCqGSM49BAMCMB8xHTAbBgNVBAMMFEZhbWlseSBD\
        Rk8gVGVzdCBSb290MB4XDTI2MDgxMDIwMjU0NloXDTI3MDkxMDIwMjU0NlowIDEeMBwGA1UEAwwV\
        b3RoZXIuZXhhbXBsZS5pbnZhbGlkMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEMN93l04C0b3K\
        bkTEESYHGdYNwJkMTU2IkOpGmCP8eZxhX2eK005O+to4nspRNHJ2WadYCvy0ejRKZWVA9wMY4qOB\
        mDCBlTAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIFoDATBgNVHSUEDDAKBggrBgEFBQcDATAg\
        BgNVHREEGTAXghVvdGhlci5leGFtcGxlLmludmFsaWQwHQYDVR0OBBYEFN6zyspvgVNr+GeUy8s2\
        G2O3EX3PMB8GA1UdIwQYMBaAFEHmaviNcBLTwUaBopuEfIASGNpcMAoGCCqGSM49BAMCA0gAMEUC\
        IQDjR2Pp2nmd/5uhqpPF1SZaIK1tgrXHgDSz/GB+EPeThQIgMuAXD/6InnTSeWvDZK9DhBeDRQ2e\
        QGNdBskR4poP024=
        """)

    static let selfSignedA = decode(
        """
        MIIB0zCCAXmgAwIBAgIJAL1b4v9JL/AuMAoGCCqGSM49BAMCMCExHzAdBgNVBAMMFnNlcnZlci5l\
        eGFtcGxlLmludmFsaWQwHhcNMjYwODEwMjAyNTQ2WhcNMjcwOTEwMjAyNTQ2WjAhMR8wHQYDVQQD\
        DBZzZXJ2ZXIuZXhhbXBsZS5pbnZhbGlkMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEFClO3qg0\
        FFFs+RcqqdLZNeEIDNqxXlUiQ2lajsJjcrbHn8e0BPiPscvw7L7JeII8rRrdr4RWq1FM4afe/Yqj\
        iqOBmTCBljAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIFoDATBgNVHSUEDDAKBggrBgEFBQcD\
        ATAhBgNVHREEGjAYghZzZXJ2ZXIuZXhhbXBsZS5pbnZhbGlkMB0GA1UdDgQWBBSZQ5d2jZPiLZZj\
        bZN7SbEeCVcUgzAfBgNVHSMEGDAWgBSZQ5d2jZPiLZZjbZN7SbEeCVcUgzAKBggqhkjOPQQDAgNI\
        ADBFAiEAuQNemw03JvpBP8aHHQw//RSI94EICaN+3F9lB/DNWdUCIHDilyFcIv+/cB1OledRyTb9\
        uYSXjtnnJd7x/lH1aAX5
        """)

    static let selfSignedB = decode(
        """
        MIIB0zCCAXmgAwIBAgIJAJnqCLGZetneMAoGCCqGSM49BAMCMCExHzAdBgNVBAMMFnNlcnZlci5l\
        eGFtcGxlLmludmFsaWQwHhcNMjYwODEwMjAyNTQ2WhcNMjcwOTEwMjAyNTQ2WjAhMR8wHQYDVQQD\
        DBZzZXJ2ZXIuZXhhbXBsZS5pbnZhbGlkMFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEXnLHvCtx\
        xiSwjrfA9rvZaQet+v4vmnPVeO/bLIWilEAEGHgHIKgLH+W5pgBcl29L/rl6LkVRXf0JmVeDdAJd\
        EaOBmTCBljAMBgNVHRMBAf8EAjAAMA4GA1UdDwEB/wQEAwIFoDATBgNVHSUEDDAKBggrBgEFBQcD\
        ATAhBgNVHREEGjAYghZzZXJ2ZXIuZXhhbXBsZS5pbnZhbGlkMB0GA1UdDgQWBBTVOyPOj7rfO6Yo\
        Sn8cM0SGGSXRfDAfBgNVHSMEGDAWgBTVOyPOj7rfO6YoSn8cM0SGGSXRfDAKBggqhkjOPQQDAgNI\
        ADBFAiEA/WzMKgbUaudT168/XmUKm9ml8I6a8R7xIvkXDa+WSJ4CICtxXtQndsyBTMCfWxFufuI0\
        olGk9tT8PSciWXXUHRL9
        """)

    private static func decode(_ base64: String) -> SecCertificate {
        guard let der = Data(base64Encoded: base64),
            let certificate = SecCertificateCreateWithData(nil, der as CFData)
        else { fatalError("fixture certificate is not valid DER") }
        return certificate
    }
}
