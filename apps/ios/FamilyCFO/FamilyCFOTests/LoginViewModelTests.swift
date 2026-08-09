import Foundation
import Testing

@testable import FamilyCFO

/// ADR 0056: the email-login (credentialed pairing) flow — the network-free
/// parts: address normalization and the step machine's guardrails.
@MainActor
struct LoginViewModelTests {
    @Test func normalizesBareHostAndPortToApiBase() {
        #expect(
            LoginViewModel.normalizedBaseURL("192.168.1.10:8443")?.absoluteString
                == "https://192.168.1.10:8443/api/v1")
    }

    @Test func acceptsFullURLAndTrailingSlash() {
        #expect(
            LoginViewModel.normalizedBaseURL("https://familycfo.local:8443/")?.absoluteString
                == "https://familycfo.local:8443/api/v1")
    }

    /// #54: the shape a person actually types. Every other case here already
    /// carried a port, which is exactly why a bare host reached 443 — where
    /// nothing listens — and reported it as a network problem.
    @Test func aBareHostGetsTheDefaultPort() {
        #expect(
            LoginViewModel.normalizedBaseURL("family-cfo-box")?.absoluteString
                == "https://family-cfo-box:8443/api/v1")
        // The case from the field: a MagicDNS name, typed without a port.
        #expect(
            LoginViewModel.normalizedBaseURL("box.example.ts.net")?.absoluteString
                == "https://box.example.ts.net:8443/api/v1")
        // A scheme does not imply 443 either — nothing serves it.
        #expect(
            LoginViewModel.normalizedBaseURL("https://box.example.ts.net")?.absoluteString
                == "https://box.example.ts.net:8443/api/v1")
    }

    /// The escape hatch for a reverse proxy: an explicit port always wins.
    @Test func anExplicitPortIsNeverOverridden() {
        #expect(
            LoginViewModel.normalizedBaseURL("box.example.ts.net:443")?.absoluteString
                == "https://box.example.ts.net:443/api/v1")
        #expect(
            LoginViewModel.normalizedBaseURL("https://box:9999/api/v1")?.absoluteString
                == "https://box:9999/api/v1")
    }

    @Test func keepsAnExistingApiPath() {
        #expect(
            LoginViewModel.normalizedBaseURL("https://box:8443/api/v1")?.absoluteString
                == "https://box:8443/api/v1")
    }

    /// #54 follow-up: the probe used to discard its error, so a certificate
    /// rejection and an unreachable host produced identical text. Two
    /// debugging sessions went at the network because of it.
    @Test func serverCheckNamesTheActualFailure() {
        let untrusted = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorServerCertificateUntrusted)
        let certMessage = LoginViewModel.describeServerCheck(untrusted)
        #expect(certMessage.contains("certificate"))
        // The raw code travels with it: the operator reading this runs the box.
        #expect(certMessage.contains("\(NSURLErrorServerCertificateUntrusted)"))

        let refused = NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost)
        #expect(LoginViewModel.describeServerCheck(refused).contains("8443"))

        // Distinct causes must not collapse into the same sentence.
        #expect(certMessage != LoginViewModel.describeServerCheck(refused))
    }

    @Test func rejectsEmptyAndGarbage() {
        #expect(LoginViewModel.normalizedBaseURL("") == nil)
        #expect(LoginViewModel.normalizedBaseURL("   ") == nil)
        #expect(LoginViewModel.normalizedBaseURL("https://") == nil)
    }

    @Test func confirmAdvancesOnlyFromConfirmStep() {
        let viewModel = LoginViewModel()
        // From enterServer, confirm is a no-op.
        viewModel.confirmServer()
        #expect(viewModel.step == .enterServer)
    }

    @Test func shortFingerprintIsHumanComparable() {
        #expect(
            LoginViewModel.shortFingerprint("abcdef0123456789") == "abcdef01…")
        #expect(LoginViewModel.shortFingerprint(nil) == "none (CA-signed)")
    }

    @Test func startOverReturnsToServerEntry() {
        let viewModel = LoginViewModel()
        viewModel.serverAddress = "not a real server"
        viewModel.startOver()
        #expect(viewModel.step == .enterServer)
    }
}
