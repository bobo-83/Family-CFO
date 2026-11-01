import Foundation
import Testing

@testable import FamilyCFO

/// Assert persisted request language BEFORE any fetch can repair it. Every
/// model uses isolated preferences/credentials and fake external session effects.
@MainActor
struct SessionRestorationTests {
    private func withCachedLanguage(
        _ language: String?, body: (SessionTestHarness) throws -> Void
    ) rethrows {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        harness.defaults.set(language, forKey: AppModel.languageKey)
        try body(harness)
    }

    private func storedSession(
        householdID: String = "h1", deviceID: String = "existing-device",
        token: String = "synthetic-existing-token", roleName: String? = nil
    ) throws -> (server: Data, credential: Data) {
        let server = ServerConfig(
            apiBaseURL: URL(string: "https://box.invalid:8443/api/v1")!,
            certificateSHA256: "saved-pin", householdID: householdID,
            householdName: "Synthetic family", deviceName: "Phone"
        )
        let credential = StoredCredential(
            deviceID: deviceID, accessToken: token, expiresAt: .distantFuture,
            role: .adult, roleName: roleName, rights: nil
        )
        return (try JSONEncoder().encode(server), try JSONEncoder().encode(credential))
    }

    @Test(arguments: ["vi", "lt"])
    func coldRelaunchPreservesTheStoredSessionsLanguage(language: String) throws {
        try withCachedLanguage(language) { harness in
            let stored = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(serverData: stored.server, credentialData: stored.credential)
            #expect(model.phase == .locked)
            #expect(model.householdLanguage == language)
            // This is precisely the source the client's language middleware reads.
            #expect(harness.storage.readLanguage() == language)
            #expect(model.credential?.accessToken == "synthetic-existing-token")
        }
    }

    @Test func signOutAndColdRelaunchUseThePersistedServerButNoCredential() async throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let stored = try storedSession()
        harness.storage.writeServer(stored.server)
        harness.storage.writeLanguage("vi")
        try harness.credentials.write(stored.credential, account: AppModel.credentialAccount)
        try harness.credentials.write(Data("synthetic-private-key".utf8), account: "device-private-key")
        let model = harness.makeModel()
        model.bootstrap() // real persistence-loading path, fake external services
        #expect(model.phase == .locked)
        #expect(model.householdLanguage == "vi")
        await model.signOut()
        #expect(model.phase == .unpaired)
        #expect(harness.storage.readServer() == stored.server)
        #expect(harness.credentials.read(AppModel.credentialAccount) == nil)
        #expect(harness.credentials.read("device-private-key") == nil)
        #expect(harness.revocations == 1)
        #expect(harness.watchDeviceIDs == [nil])

        let relaunched = harness.makeModel()
        relaunched.bootstrap()
        #expect(relaunched.phase == .unpaired)
        #expect(relaunched.credential == nil)
        #expect(relaunched.householdLanguage == "en")
        #expect(relaunched.server?.certificateSHA256 == "saved-pin")
        let login = LoginViewModel()
        login.restoreServer(relaunched.server)
        #expect(login.step == .credentials(
            baseURL: URL(string: "https://box.invalid:8443/api/v1")!, fingerprint: "saved-pin"
        ))
        #expect(harness.artifactClears == 2) // fake cleanup only, no host services
    }

    @Test func missingLanguageCacheDefaultsToEnglish() throws {
        try withCachedLanguage(nil) { harness in
            let stored = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(serverData: stored.server, credentialData: stored.credential)
            #expect(model.phase == .locked)
            #expect(model.householdLanguage == "en")
            #expect(harness.storage.readLanguage() == "en")
        }
    }

    @Test func restoringTheSameTokenWithNewRightsKeepsLanguageAndFreshness() throws {
        try withCachedLanguage("vi") { harness in
            let stored = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(serverData: stored.server, credentialData: stored.credential)
            model.householdLanguage = "lt"
            let freshness = model.syncStatus
            let updated = try storedSession(roleName: "Updated role")
            model.restoreSession(serverData: updated.server, credentialData: updated.credential)
            #expect(model.householdLanguage == "lt")
            #expect(harness.storage.readLanguage() == "lt")
            #expect(model.syncStatus === freshness)
            #expect(model.credential?.roleName == "Updated role")
        }
    }

    @Test(arguments: [false, true])
    func newSessionDoesNotInheritLanguageEvenWithinTheSameHousehold(
        switchesHousehold: Bool
    ) throws {
        try withCachedLanguage("vi") { harness in
            let first = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(serverData: first.server, credentialData: first.credential)
            model.householdLanguage = "lt"
            let replacement = try storedSession(
                householdID: switchesHousehold ? "h2" : "h1",
                deviceID: "new-device", token: "synthetic-new-token"
            )
            model.restoreSession(serverData: replacement.server, credentialData: replacement.credential)
            #expect(model.phase == .locked)
            #expect(model.householdLanguage == "en")
            #expect(harness.storage.readLanguage() == "en")
        }
    }

    @Test(arguments: [false, true])
    func signedOutOrCorruptCredentialRestorationClearsStaleLanguage(corrupt: Bool) throws {
        try withCachedLanguage("vi") { harness in
            let stored = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(
                serverData: stored.server,
                credentialData: corrupt ? Data("invalid-credential".utf8) : nil
            )
            #expect(model.phase == .unpaired)
            #expect(model.server != nil) // retained-server re-entry still works
            #expect(model.credential == nil)
            #expect(model.householdLanguage == "en")
            #expect(harness.storage.readLanguage() == "en")
        }
    }

    @Test(arguments: [false, true])
    func missingOrCorruptServerCannotReviveCredentialOrLanguage(corrupt: Bool) throws {
        try withCachedLanguage("lt") { harness in
            let stored = try storedSession()
            let model = harness.makeModel()
            model.restoreSession(
                serverData: corrupt ? Data("invalid-server".utf8) : nil,
                credentialData: stored.credential
            )
            #expect(model.phase == .unpaired)
            #expect(model.server == nil)
            #expect(model.credential == nil)
            #expect(harness.storage.readLanguage() == "en")
        }
    }
}
