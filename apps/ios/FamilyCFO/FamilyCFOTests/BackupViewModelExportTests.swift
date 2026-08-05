import Foundation
import Testing

@testable import FamilyCFO

/// "Export my data" (#189): the whole-household zip lands in a temp file the
/// share sheet can hand off; a 423 (sealed household, locked) carries the
/// server's human message verbatim. Only the export path is exercised — the
/// rest of BackupAPI is unreachable from these tests and just throws.
@MainActor
final class MockExportBackupAPI: BackupAPI, @unchecked Sendable {
    var exportResult: Result<Data, Error> = .success(Data())

    nonisolated func exportData() async throws -> Data {
        try await MainActor.run { try exportResult.get() }
    }

    // Unused by the export tests.
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
    nonisolated func householdKeyStatus() async throws -> Components.Schemas.HouseholdKeyStatus {
        throw APIError.server(500)
    }
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
}

@MainActor
struct BackupViewModelExportTests {
    @Test func exportWritesTheZipToATempFileForSharing() async throws {
        let api = MockExportBackupAPI()
        api.exportResult = .success(Data("zip-bytes".utf8))
        let vm = BackupViewModel(api: api)

        await vm.exportData()

        let url = try #require(vm.exportedFileURL)
        #expect(url.lastPathComponent.hasPrefix("family-cfo-export-"))
        #expect(url.pathExtension == "zip")
        #expect(try Data(contentsOf: url) == Data("zip-bytes".utf8))
        #expect(vm.errorMessage == nil)
        try? FileManager.default.removeItem(at: url)
    }

    @Test func aLockedHouseholdSurfacesTheServersMessageVerbatim() async {
        let api = MockExportBackupAPI()
        let detail = "This household is sealed and currently locked. Sign in again to unlock it."
        api.exportResult = .failure(APIError.advisor(detail))
        let vm = BackupViewModel(api: api)

        await vm.exportData()

        #expect(vm.errorMessage == detail)
        #expect(vm.exportedFileURL == nil)
    }
}
