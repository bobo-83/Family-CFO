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

    @Test func keepsAnExistingApiPath() {
        #expect(
            LoginViewModel.normalizedBaseURL("https://box:8443/api/v1")?.absoluteString
                == "https://box:8443/api/v1")
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
