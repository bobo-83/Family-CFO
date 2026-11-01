import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

private struct SignInFailureTransport: ClientTransport {
    enum Failure: CaseIterable, Sendable {
        case tls, decoding, unauthorized, timedOut, cancelled, unexpectedStatus
    }
    let failure: Failure

    func send(
        _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        switch failure {
        case .tls:
            throw NSError(domain: "outer-secret-domain", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "password=never-print-this",
                NSUnderlyingErrorKey: NSError(
                    domain: NSURLErrorDomain, code: NSURLErrorServerCertificateUntrusted,
                    userInfo: [NSLocalizedDescriptionKey: "https://secret.invalid/?token=secret"]
                ),
            ])
        case .decoding:
            return (HTTPResponse(status: .created, headerFields: [.contentType: "application/json"]),
                    HTTPBody(Data("not-json-secret".utf8)))
        case .timedOut:
            throw URLError(.timedOut)
        case .cancelled:
            throw CancellationError()
        case .unexpectedStatus:
            return (HTTPResponse(status: .internalServerError),
                    HTTPBody(Data("secret-server-body".utf8)))
        case .unauthorized:
            return (HTTPResponse(status: .unauthorized, headerFields: [.contentType: "application/json"]),
                    HTTPBody(Data(#"{"error":{"code":"http_error","message":"secret"}}"#.utf8)))
        }
    }
}

private struct SuccessfulSignInTransport: ClientTransport {
    func send(
        _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let payload = Data(#"{"device_id":"synthetic-device","access_token":"synthetic-token","expires_at":"2099-01-01T00:00:00Z","role":"adult","household_id":"h1","household_name":"Synthetic family"}"#.utf8)
        return (HTTPResponse(status: .created, headerFields: [.contentType: "application/json"]),
                HTTPBody(payload))
    }
}

private final class SignInCleanupTransport: ClientTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var recorded: [HTTPRequest] = []
    let fails: Bool

    init(fails: Bool) { self.fails = fails }

    var requests: [HTTPRequest] { lock.withLock { recorded } }

    func send(
        _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        lock.withLock { recorded.append(request) }
        if fails { throw URLError(.notConnectedToInternet) }
        return (HTTPResponse(status: .noContent), nil)
    }
}

private final class SignInClientRecorder: @unchecked Sendable {
    struct Configuration: Equatable, Sendable {
        let url: URL
        let pin: String?
    }

    private let lock = NSLock()
    private var values: [Configuration] = []

    func record(_ url: URL, _ pin: String?) {
        lock.withLock { values.append(Configuration(url: url, pin: pin)) }
    }

    var configurations: [Configuration] { lock.withLock { values } }
}

@MainActor
struct AccountSignInTests {
    private let url = URL(string: "https://box.invalid:8443/api/v1")!

    private func attemptSignIn(_ viewModel: LoginViewModel) async {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        await viewModel.signIn(into: harness.makeModel())
    }

    @Test func rejectsInsecureOrCredentialBearingAddresses() {
        for address in ["http://box", "ftp://box", "https://user:secret@box"] {
            #expect(LoginViewModel.normalizedBaseURL(address) == nil)
        }
    }

    @Test func reentryUsesSavedTrustWithoutAProbe() {
        let vm = LoginViewModel()
        let server = ServerConfig(apiBaseURL: url, certificateSHA256: "saved-pin",
                                  householdID: "old", householdName: "Old", deviceName: "Phone")
        vm.restoreServer(server)
        #expect(vm.step == .credentials(baseURL: url, fingerprint: "saved-pin"))
        vm.password = "synthetic"
        vm.startOver()
        #expect(vm.password.isEmpty)
        #expect(vm.step == .enterServer)
    }

    @Test func retainedServerDataRestoresIndependentlyOfCredential() throws {
        let server = ServerConfig(apiBaseURL: url, certificateSHA256: "saved-pin",
                                  householdID: "old", householdName: "Old", deviceName: "Phone")
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = harness.makeModel()
        model.restoreSession(serverData: try JSONEncoder().encode(server), credentialData: nil)
        #expect(model.phase == .unpaired)
        #expect(model.server == server)
        #expect(model.credential == nil)
        let login = LoginViewModel()
        login.restoreServer(model.server)
        #expect(login.step == .credentials(baseURL: url, fingerprint: "saved-pin"))
    }

    @Test func existingQRAndAccountCredentialsNeedNoOriginMigration() throws {
        // Both historical bootstrap paths stored exactly this shape, including
        // credentials predating optional rights/roleName fields.
        let server = ServerConfig(apiBaseURL: url, certificateSHA256: "old-pin",
                                  householdID: "h1", householdName: "Family", deviceName: "Phone")
        let data = Data(#"{"deviceID":"existing-device","accessToken":"synthetic-token","expiresAt":900000000,"role":"adult"}"#.utf8)
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = harness.makeModel()
        model.restoreSession(serverData: try JSONEncoder().encode(server), credentialData: data)
        #expect(model.phase == .locked)
        #expect(model.server?.certificateSHA256 == "old-pin")
        #expect(model.credential?.deviceID == "existing-device")
        #expect(model.credential?.accessToken == "synthetic-token")
    }

    @Test func generatedClientErrorsAreUnwrappedAndSanitized() async {
        for failure in [SignInFailureTransport.Failure.tls, .decoding] {
            let vm = LoginViewModel(
                step: .credentials(baseURL: url, fingerprint: "saved-pin"),
                buildClient: { url, _ in
                    Client(serverURL: url, transport: SignInFailureTransport(failure: failure))
                }
            )
            vm.email = "synthetic@example.invalid"
            vm.password = "synthetic"
            await attemptSignIn(vm)
            #expect(vm.step == .credentials(baseURL: url, fingerprint: "saved-pin"))
            guard let message = vm.signInError else {
                Issue.record("Expected a safe inline failure on the same trusted server")
                continue
            }
            #expect(!message.contains("secret"))
            #expect(!message.contains("synthetic"))
            if case .tls = failure {
                #expect(message.contains("certificate"))
                #expect(message.contains("-1202"))
            } else {
                #expect(message.contains("decoded"))
                #expect(!message.contains("reach"))
            }
        }
    }

    @Test(arguments: [Optional("saved-pin"), nil])
    func wrongPasswordStaysOnSameTrustedServerAndClearsPassword(pin: String?) async {
        let vm = LoginViewModel(
            step: .credentials(baseURL: url, fingerprint: pin),
            buildClient: { url, _ in
                Client(serverURL: url, transport: SignInFailureTransport(failure: .unauthorized))
            }
        )
        vm.password = "synthetic"
        await attemptSignIn(vm)
        #expect(vm.step == .credentials(baseURL: url, fingerprint: pin))
        #expect(vm.password.isEmpty)
        #expect(vm.signInError == "Wrong email or password.")
    }

    @Test func accountSwitchDropsLanguageAdvisorAskAndSyncFreshness() throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = harness.makeModel()
        let oldServer = ServerConfig(apiBaseURL: url, certificateSHA256: nil,
                                     householdID: "old", householdName: "Old", deviceName: "Phone")
        let newServer = ServerConfig(apiBaseURL: url, certificateSHA256: nil,
                                     householdID: "new", householdName: "New", deviceName: "Phone")
        func credential(_ id: String) throws -> Data {
            try JSONEncoder().encode(StoredCredential(
                deviceID: id, accessToken: "synthetic-\(id)", expiresAt: .distantFuture,
                role: .adult
            ))
        }
        model.restoreSession(serverData: try JSONEncoder().encode(oldServer),
                             credentialData: try credential(UUID().uuidString))
        model.householdLanguage = "vi"
        model.askAdvisor("synthetic old-session question")
        let outgoingFreshness = model.syncStatus
        outgoingFreshness.markSynced()
        model.restoreSession(serverData: try JSONEncoder().encode(newServer),
                             credentialData: try credential(UUID().uuidString))
        #expect(model.householdLanguage == "en")
        #expect(model.advisorAsk == nil)
        #expect(model.syncStatus.lastSyncedAt == nil)
        outgoingFreshness.markSynced()
        #expect(model.syncStatus.lastSyncedAt == nil)
    }

    @Test(arguments: [Optional("saved-pin"), nil], SignInFailureTransport.Failure.allCases)
    private func repeatedFailuresKeepTheExactTrustedEndpoint(
        pin: String?, failure: SignInFailureTransport.Failure
    ) async {
        // Explicit 443 and a proxy prefix must survive retries unchanged, too.
        let endpoint = URL(string: "https://box.invalid:443/finance/api/v1")!
        let recorder = SignInClientRecorder()
        let vm = LoginViewModel(buildClient: { url, fingerprint in
            recorder.record(url, fingerprint)
            return Client(serverURL: url, transport: SignInFailureTransport(failure: failure))
        })
        vm.restoreServer(ServerConfig(
            apiBaseURL: endpoint, certificateSHA256: pin,
            householdID: "h1", householdName: "Family", deviceName: "Phone"
        ))
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = harness.makeModel()
        for _ in 0..<2 {
            vm.email = "synthetic@example.invalid"
            vm.password = "synthetic"
            await vm.signIn(into: model)
            // Retry is immediately available without calling checkServer or
            // confirming another certificate. An unexpected status is no exception.
            #expect(vm.step == .credentials(baseURL: endpoint, fingerprint: pin))
            #expect(vm.signInError?.isEmpty == false)
            #expect(vm.signInError?.contains("secret") == false)
            #expect(model.credential == nil)
        }
        #expect(recorder.configurations == Array(
            repeating: SignInClientRecorder.Configuration(url: endpoint, pin: pin), count: 2
        ))
    }

    @Test func onlyExplicitServerChangeLeavesTheSavedTrustFlow() async {
        let vm = LoginViewModel(
            step: .credentials(baseURL: url, fingerprint: "saved-pin"),
            buildClient: { url, _ in
                Client(serverURL: url, transport: SignInFailureTransport(failure: .tls))
            }
        )
        vm.serverAddress = url.absoluteString
        vm.password = "synthetic"
        await attemptSignIn(vm)
        #expect(vm.step == .credentials(baseURL: url, fingerprint: "saved-pin"))
        vm.startOver() // the Change server button, not an ordinary retry
        #expect(vm.step == .enterServer)
        #expect(vm.password.isEmpty)
        #expect(vm.signInError == nil)
        vm.restoreServer(ServerConfig(
            apiBaseURL: url, certificateSHA256: "saved-pin",
            householdID: "h1", householdName: "Family", deviceName: "Phone"
        ))
        #expect(vm.step == .enterServer) // root task cannot undo the explicit choice
    }

    @Test(arguments: [Optional("saved-pin"), nil], ["device-private-key", AppModel.credentialAccount])
    func persistenceFailuresKeepTrustAndRevokeTheIssuedSession(
        pin: String?, failedAccount: String
    ) async {
        await checkPersistenceFailure(pin: pin, failedAccount: failedAccount, cleanupFails: false)
    }

    @Test(arguments: [Optional("saved-pin"), nil], ["device-private-key", AppModel.credentialAccount])
    func failedRevocationDoesNotHideTheStorageFailureOrCompleteSignIn(
        pin: String?, failedAccount: String
    ) async {
        await checkPersistenceFailure(pin: pin, failedAccount: failedAccount, cleanupFails: true)
    }

    private func checkPersistenceFailure(
        pin: String?, failedAccount: String, cleanupFails: Bool
    ) async {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        harness.credentials.failWrites(to: failedAccount)
        let credentials = harness.credentials
        let cleanup = SignInCleanupTransport(fails: cleanupFails)
        let cleanupConfiguration = SignInClientRecorder()
        let endpoint = URL(string: "https://box.invalid:443/finance/api/v1")!
        let vm = LoginViewModel(
            step: .credentials(baseURL: endpoint, fingerprint: pin),
            savePrivateKey: { try credentials.write($0, account: "device-private-key") },
            deletePrivateKey: { credentials.delete("device-private-key") },
            buildRevocationClient: { url, fingerprint, token in
                cleanupConfiguration.record(url, fingerprint)
                return Client(serverURL: url, transport: cleanup,
                              middlewares: [BearerAuthMiddleware(token: { token })])
            },
            buildClient: { url, _ in Client(serverURL: url, transport: SuccessfulSignInTransport()) }
        )
        vm.email = "synthetic@example.invalid"
        vm.password = "synthetic"
        let model = harness.makeModel()
        model.bootstrap()
        await vm.signIn(into: model)
        #expect(vm.step == .credentials(baseURL: endpoint, fingerprint: pin))
        #expect(vm.signInError == "Could not securely save this device’s sign-in. Try signing in again.")
        #expect(vm.password.isEmpty)
        #expect(cleanupConfiguration.configurations == [.init(url: endpoint, pin: pin)])
        #expect(cleanup.requests.count == 1)
        #expect(cleanup.requests.first?.method == .delete)
        #expect(cleanup.requests.first?.path?.hasSuffix("/auth/sessions") == true)
        #expect(cleanup.requests.first?.headerFields[.authorization] == "Bearer synthetic-token")
        #expect(credentials.read("device-private-key") == nil)
        #expect(model.phase == .unpaired)
        #expect(model.credential == nil)
        #expect(harness.credentials.read(AppModel.credentialAccount) == nil)
        #expect(harness.storage.readServer() == nil)
        #expect(harness.watchActivations == 0)
        #expect(harness.watchDeviceIDs.isEmpty)
        #expect(harness.artifactClears == 0)
        let expected = failedAccount == "device-private-key"
            ? ["device-private-key"] : ["device-private-key", AppModel.credentialAccount]
        #expect(harness.credentials.writeAttempts == expected)
    }

    @Test(arguments: ["http://box.invalid/api/v1", "ftp://box.invalid/api/v1",
                      "https://user:secret@box.invalid/api/v1"])
    func unsafeCredentialStepNeverBuildsOrSendsAClient(address: String) async {
        let recorder = SignInClientRecorder()
        let vm = LoginViewModel(
            step: .credentials(baseURL: URL(string: address)!, fingerprint: nil),
            buildClient: { url, fingerprint in
                recorder.record(url, fingerprint)
                return Client(serverURL: url, transport: SignInFailureTransport(failure: .unauthorized))
            }
        )
        await attemptSignIn(vm)
        #expect(recorder.configurations.isEmpty)
        guard case .failed = vm.step else {
            Issue.record("Unsafe credential destinations must fail before transport construction")
            return
        }
    }

    @Test func persistedSyncFreshnessIsScopedAndIgnoresLegacyGlobalCache() throws {
        let suite = "AccountSignInTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(Date(), forKey: "familyCFO.lastSyncedAt")
        let old = SyncStatusModel(scope: "old-device", defaults: defaults)
        let new = SyncStatusModel(scope: "new-device", defaults: defaults)
        #expect(old.lastSyncedAt == nil)
        old.markSynced()
        #expect(new.lastSyncedAt == nil)
        #expect(SyncStatusModel(scope: "old-device", defaults: defaults).lastSyncedAt != nil)
    }

    @Test func URLSessionTLSClassificationSurvivesItsUnderlyingSSLDetail() {
        let error = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorServerCertificateUntrusted,
            userInfo: [NSUnderlyingErrorKey: NSError(
                domain: "private-ssl-domain", code: -9807,
                userInfo: [NSLocalizedDescriptionKey: "https://secret.invalid/?token=secret"]
            )]
        )
        let message = SignInErrorDescriber.describe(error)
        #expect(message.contains("certificate"))
        #expect(message.contains("-1202"))
        #expect(!message.contains("secret"))
        #expect(!message.contains("-9807"))
    }

    @Test func unknownAndCancelledErrorsAreNotConnectivityClaims() {
        let unknown = NSError(domain: "secret-domain", code: 99,
                              userInfo: [NSLocalizedDescriptionKey: "password=secret"])
        let message = SignInErrorDescriber.describe(unknown)
        #expect(message.contains("cause is not known"))
        #expect(!message.contains("secret"))
        #expect(SignInErrorDescriber.describe(CancellationError()).contains("cancelled"))
    }
}
