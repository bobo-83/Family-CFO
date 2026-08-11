import Foundation
import Testing

@testable import FamilyCFO

/// #96 (ADR 0072 Phase 3): the Settings → Household offer to seal. Only the
/// key-status call matters here; the rest of BackupAPI is unreachable from
/// these tests and just throws.
@MainActor
final class MockKeyStatusBackupAPI: BackupAPI, @unchecked Sendable {
    var keyStatus: Components.Schemas.HouseholdKeyStatus?
    private(set) var keyStatusCalls = 0

    nonisolated func householdKeyStatus() async throws -> Components.Schemas.HouseholdKeyStatus {
        try await MainActor.run {
            keyStatusCalls += 1
            guard let keyStatus else { throw APIError.server(500) }
            return keyStatus
        }
    }

    // Unused by these tests.
    nonisolated func config() async throws -> Components.Schemas.BackupConfig {
        throw APIError.server(500)
    }
    nonisolated func updateConfig(_ update: BackupConfigDraft) async throws
        -> Components.Schemas.BackupConfig
    { throw APIError.server(500) }
    nonisolated func checkConnection(_ draft: BackupConfigDraft) async throws
        -> Components.Schemas.BackupDestinationCheckResponse
    { throw APIError.server(500) }
    nonisolated func backupNow() async throws -> Components.Schemas.BackupJob {
        throw APIError.server(500)
    }
    nonisolated func localBackups() async throws -> [Components.Schemas.BackupJob] { [] }
    nonisolated func restoreLocal(id: String) async throws { throw APIError.server(500) }
    nonisolated func remoteBackups() async throws -> [Components.Schemas.RemoteBackup] { [] }
    nonisolated func restoreRemote(filename: String) async throws { throw APIError.server(500) }
    nonisolated func deleteLocal(id: String) async throws { throw APIError.server(500) }
    nonisolated func deleteRemote(filename: String) async throws { throw APIError.server(500) }
    nonisolated func encryptionKey() async throws -> String? { nil }
    nonisolated func generateRecoveryKey() async throws -> Components.Schemas.RecoveryKey {
        throw APIError.server(500)
    }
    nonisolated func setSealMode(_ mode: Components.Schemas.SealModeRequest.ModePayload)
        async throws -> Components.Schemas.HouseholdKeyStatus
    { throw APIError.server(500) }
    nonisolated func unlockWithRecoveryKey(_ key: String) async throws
        -> Components.Schemas.HouseholdKeyStatus
    { throw APIError.server(500) }
    nonisolated func serverVersion() async -> String? { nil }
    nonisolated func exportData() async throws -> Data { throw APIError.server(500) }
}

@MainActor
struct SealedModeOfferViewModelTests {
    private func status(
        encryptionEnabled: Bool = true,
        memberWraps: Int = 2,
        hasRecoveryKey: Bool = true,
        mode: Components.Schemas.HouseholdKeyStatus.ModePayload? = .convenient
    ) -> Components.Schemas.HouseholdKeyStatus {
        .init(
            encryptionEnabled: encryptionEnabled,
            memberWraps: memberWraps,
            deviceWraps: 1,
            hasRecoveryKey: hasRecoveryKey,
            recoveryKeyCreatedAt: nil,
            mode: mode,
            unlocked: true)
    }

    /// A fresh, isolated defaults suite per test — the offer is dismissed per
    /// device, so the real UserDefaults must never leak between tests.
    private func defaults(_ name: String = UUID().uuidString) -> UserDefaults {
        UserDefaults(suiteName: name) ?? .standard
    }

    @Test func offersSealingWhenTheHouseholdCouldSeal() async {
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = status()
        let model = SealedModeOfferViewModel(api: api, defaults: defaults())

        await model.load()

        #expect(model.isOffered)
        #expect(!model.needsRecoveryKey)
        #expect(!model.needsMemberKey)
    }

    @Test func namesTheMissingPreconditionsInsteadOfHidingTheOffer() async {
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = status(memberWraps: 0, hasRecoveryKey: false)
        let model = SealedModeOfferViewModel(api: api, defaults: defaults())

        await model.load()

        #expect(model.isOffered)
        #expect(model.needsRecoveryKey)
        #expect(model.needsMemberKey)
    }

    @Test func staysQuietForAnAlreadySealedHousehold() async {
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = status(mode: .sealed)
        let model = SealedModeOfferViewModel(api: api, defaults: defaults())

        await model.load()

        #expect(!model.isOffered)
    }

    @Test func staysQuietWhenTheBoxDoesNotEncryptPerHousehold() async {
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = status(encryptionEnabled: false, memberWraps: 0, hasRecoveryKey: false)
        let model = SealedModeOfferViewModel(api: api, defaults: defaults())

        await model.load()

        #expect(!model.isOffered)
    }

    @Test func staysQuietWhenTheBoxCannotAnswer() async {
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = nil
        let model = SealedModeOfferViewModel(api: api, defaults: defaults())

        await model.load()

        #expect(!model.isOffered)
    }

    @Test func staysDismissedOnThisDeviceAcrossLaunches() async {
        let suite = UUID().uuidString
        let api = MockKeyStatusBackupAPI()
        api.keyStatus = status()
        let model = SealedModeOfferViewModel(api: api, defaults: defaults(suite))
        await model.load()
        #expect(model.isOffered)

        model.dismiss()
        #expect(!model.isOffered)

        // A fresh launch on the same phone: not shown, and not even asked for.
        let relaunched = SealedModeOfferViewModel(api: api, defaults: defaults(suite))
        await relaunched.load()
        #expect(!relaunched.isOffered)
        #expect(api.keyStatusCalls == 1)
    }
}
