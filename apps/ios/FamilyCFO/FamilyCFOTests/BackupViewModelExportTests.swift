import Foundation
import Testing

@testable import FamilyCFO

private actor ControlledExportEndpoint {
    private var queued: [Result<Data, Error>] = []
    private var pending: [Int: CheckedContinuation<Data, Error>] = [:]
    private var nextID = 0
    private var calls = 0

    func enqueue(_ result: Result<Data, Error>) { queued.append(result) }

    func call() async throws -> Data {
        calls += 1
        let id = nextID
        nextID += 1
        if !queued.isEmpty { return try queued.removeFirst().get() }
        return try await withCheckedThrowingContinuation { pending[id] = $0 }
    }

    func waitForCalls(_ count: Int) async {
        while calls < count { await Task.yield() }
    }

    func resolve(_ id: Int, _ result: Result<Data, Error>) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(with: result)
    }
}

/// "Export my data" (#189): the whole-household zip lands in a temp file the
/// share sheet can hand off; a 423 (sealed household, locked) carries the
/// server's human message verbatim. Only the export path is exercised — the
/// rest of BackupAPI is unreachable from these tests and just throws.
final class MockExportBackupAPI: BackupAPI, @unchecked Sendable {
    fileprivate nonisolated let exports = ControlledExportEndpoint()

    nonisolated func exportData() async throws -> Data {
        try await exports.call()
    }

    // Unused by the export tests.
    nonisolated func config() async throws -> Components.Schemas.BackupConfig {
        throw APIError.server(500)
    }
    nonisolated func updateConfig(_ update: BackupConfigDraft) async throws
        -> Components.Schemas.BackupConfig
    { throw APIError.server(500) }
    nonisolated func recoveryStatus() async throws -> Components.Schemas.BackupRecoveryStatus {
        throw APIError.server(500)
    }
    nonisolated func checkConnection(_ draft: BackupConfigDraft) async throws
        -> Components.Schemas.BackupDestinationCheckResponse
    { throw APIError.server(500) }
    nonisolated func backupNow() async throws -> Components.Schemas.BackupJob {
        throw APIError.server(500)
    }
    nonisolated func localBackups() async throws -> [Components.Schemas.BackupJob] { [] }
    nonisolated func restoreLocal(id: String) async throws { throw APIError.server(500) }
    nonisolated func remoteBackups() async throws
        -> Components.Schemas.RemoteBackupListResponse
    { .init(backups: [], status: .available, asOf: Date()) }
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

@Suite(.serialized)
@MainActor
struct BackupViewModelExportTests {
    private func exportURLs() -> Set<URL> {
        let urls = try? FileManager.default.contentsOfDirectory(
            at: FileManager.default.temporaryDirectory,
            includingPropertiesForKeys: nil
        )
        return Set((urls ?? []).filter {
            $0.lastPathComponent.hasPrefix("family-cfo-export-") && $0.pathExtension == "zip"
        })
    }

    @Test func exportWritesTheZipToATempFileForSharing() async throws {
        let api = MockExportBackupAPI()
        await api.exports.enqueue(.success(Data("zip-bytes".utf8)))
        let vm = BackupViewModel(api: api)

        let returnedURL = await vm.exportData()

        let url = try #require(vm.exportedFileURL)
        #expect(returnedURL == url)
        #expect(url.lastPathComponent.hasPrefix("family-cfo-export-"))
        #expect(url.pathExtension == "zip")
        #expect(try Data(contentsOf: url) == Data("zip-bytes".utf8))
        #expect(vm.errorMessage == nil)
        try? FileManager.default.removeItem(at: url)
    }

    @Test func viewDisappearanceBeforeResolutionWritesNoTemporaryFile() async {
        let api = MockExportBackupAPI()
        let vm = BackupViewModel(api: api)
        let filesBefore = exportURLs()

        let export = Task { await vm.exportData() }
        await api.exports.waitForCalls(1)
        vm.endViewLifetime()
        await api.exports.resolve(0, .success(Data("late-export".utf8)))

        #expect(await export.value == nil)
        #expect(exportURLs() == filesBefore)
        #expect(vm.exportedFileURL == nil)
        #expect(!vm.isExporting)
    }

    @Test func viewDisappearanceAfterResolutionDeletesExactPublishedFile() async throws {
        let api = MockExportBackupAPI()
        await api.exports.enqueue(.success(Data("ready-export".utf8)))
        let vm = BackupViewModel(api: api)

        let url = try #require(await vm.exportData())
        #expect(FileManager.default.fileExists(atPath: url.path))

        // Simulates disappearance in the async return-to-view scheduling gap.
        vm.endViewLifetime()
        #expect(!vm.claimExportForPresentation(url))
        #expect(!FileManager.default.fileExists(atPath: url.path))
        #expect(vm.exportedFileURL == nil)
    }

    @Test func sessionReplacementDeletesAnAlreadyPublishedExport() async throws {
        let api = MockExportBackupAPI()
        var session = "household-a:session-a"
        let vm = BackupViewModel(api: api, sessionIdentity: { session })
        await api.exports.enqueue(.success(Data("settled-session-a-export".utf8)))

        let sessionAURL = try #require(await vm.exportData())
        #expect(vm.exportedFileURL == sessionAURL)
        #expect(FileManager.default.fileExists(atPath: sessionAURL.path))

        session = "household-b:session-b"
        #expect(vm.replaceSessionIfNeeded(with: session))
        #expect(vm.exportedFileURL == nil)
        #expect(!FileManager.default.fileExists(atPath: sessionAURL.path))
        #expect(!vm.isExporting)
    }

    @Test func consecutiveExportsUseDistinctFilesAndDismissalRemovesOnlyItsFile() async throws {
        let api = MockExportBackupAPI()
        await api.exports.enqueue(.success(Data("first-export".utf8)))
        await api.exports.enqueue(.success(Data("second-export".utf8)))
        let vm = BackupViewModel(api: api)

        let firstURL = try #require(await vm.exportData())
        let secondURL = try #require(await vm.exportData())

        #expect(firstURL != secondURL)
        #expect(try Data(contentsOf: firstURL) == Data("first-export".utf8))
        #expect(try Data(contentsOf: secondURL) == Data("second-export".utf8))
        vm.discardExportedFile(firstURL)
        #expect(!FileManager.default.fileExists(atPath: firstURL.path))
        #expect(vm.exportedFileURL == secondURL)
        vm.discardExportedFile(secondURL)
        #expect(!FileManager.default.fileExists(atPath: secondURL.path))
        #expect(vm.exportedFileURL == nil)
    }

    @Test func aLockedHouseholdSurfacesTheServersMessageVerbatim() async {
        let api = MockExportBackupAPI()
        let detail = "This household is sealed and currently locked. Sign in again to unlock it."
        await api.exports.enqueue(.failure(APIError.advisor(detail)))
        let vm = BackupViewModel(api: api)

        let returnedURL = await vm.exportData()

        #expect(returnedURL == nil)
        #expect(vm.errorMessage == detail)
        #expect(vm.exportedFileURL == nil)
    }

    @Test func oldSessionExportWritesNoFileAndCannotClearTheCurrentSlot() async throws {
        let api = MockExportBackupAPI()
        var session = "household-a:session-a"
        let vm = BackupViewModel(api: api, sessionIdentity: { session })
        let filesBeforeStaleCompletion = exportURLs()

        let stale = Task { await vm.exportData() }
        await api.exports.waitForCalls(1)
        session = "household-b:session-b"
        let current = Task { await vm.exportData() }
        await api.exports.waitForCalls(2)

        await api.exports.resolve(0, .success(Data("old-session-bytes".utf8)))
        #expect(await stale.value == nil)
        #expect(exportURLs() == filesBeforeStaleCompletion)
        #expect(vm.exportedFileURL == nil)
        #expect(vm.isExporting)
        #expect(vm.errorMessage == nil)

        await api.exports.resolve(1, .success(Data("current-session-bytes".utf8)))
        let currentURL = try #require(await current.value)
        #expect(currentURL == vm.exportedFileURL)
        #expect(try Data(contentsOf: currentURL) == Data("current-session-bytes".utf8))
        #expect(!vm.isExporting)
        vm.discardExportedFile(currentURL)
    }
}
