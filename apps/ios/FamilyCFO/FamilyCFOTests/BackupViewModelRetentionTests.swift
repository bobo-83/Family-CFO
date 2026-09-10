import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

private actor ImmediateEndpoint<Value: Sendable> {
    private var result: Result<Value, Error>

    init(_ result: Result<Value, Error>) { self.result = result }
    func set(_ result: Result<Value, Error>) { self.result = result }
    func call() throws -> Value { try result.get() }
}

private actor ControlledEndpoint<Value: Sendable> {
    private var queued: [Result<Value, Error>] = []
    private var pending: [Int: CheckedContinuation<Value, Error>] = [:]
    private var nextID = 0
    private var calls = 0

    func enqueue(_ result: Result<Value, Error>) { queued.append(result) }

    func call() async throws -> Value {
        calls += 1
        let id = nextID
        nextID += 1
        if !queued.isEmpty { return try queued.removeFirst().get() }
        return try await withCheckedThrowingContinuation { pending[id] = $0 }
    }

    func waitForCalls(_ count: Int) async {
        while calls < count { await Task.yield() }
    }

    func resolve(_ id: Int, _ result: Result<Value, Error>) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(with: result)
    }

    func callCount() -> Int { calls }
}

private final class RemoteDeleteTransport: ClientTransport, @unchecked Sendable {
    let response: Components.Schemas.BackupDestinationCheckResponse

    init(response: Components.Schemas.BackupDestinationCheckResponse) {
        self.response = response
    }

    func send(
        _ request: HTTPRequest,
        body: HTTPBody?,
        baseURL: URL,
        operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let payload = try encoder.encode(response)
        return (
            HTTPResponse(status: .ok, headerFields: [.contentType: "application/json"]),
            HTTPBody(payload))
    }
}

private actor ControlledUpdates {
    private var queued: [Result<Components.Schemas.BackupConfig, Error>] = []
    private var pending: [Int: CheckedContinuation<Components.Schemas.BackupConfig, Error>] = [:]
    private var nextID = 0
    private var received: [BackupConfigDraft] = []
    private var active = 0
    private var maximumActive = 0

    func enqueue(_ result: Result<Components.Schemas.BackupConfig, Error>) { queued.append(result) }

    func call(_ draft: BackupConfigDraft) async throws -> Components.Schemas.BackupConfig {
        received.append(draft)
        active += 1
        maximumActive = max(maximumActive, active)
        defer { active -= 1 }
        let id = nextID
        nextID += 1
        if !queued.isEmpty { return try queued.removeFirst().get() }
        return try await withCheckedThrowingContinuation { pending[id] = $0 }
    }

    func waitForCalls(_ count: Int) async {
        while received.count < count { await Task.yield() }
    }

    func resolve(_ id: Int, _ result: Result<Components.Schemas.BackupConfig, Error>) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(with: result)
    }

    func drafts() -> [BackupConfigDraft] { received }
    func maxConcurrent() -> Int { maximumActive }
}

private final class MockRetentionBackupAPI: BackupAPI, @unchecked Sendable {
    nonisolated let configs = ControlledEndpoint<Components.Schemas.BackupConfig>()
    nonisolated let updates = ControlledUpdates()
    nonisolated let statuses = ControlledEndpoint<Components.Schemas.BackupRecoveryStatus>()
    nonisolated let checks = ControlledEndpoint<Components.Schemas.BackupDestinationCheckResponse>()
    nonisolated let backupJobs = ControlledEndpoint<Components.Schemas.BackupJob>()
    nonisolated let localRestores = ControlledEndpoint<Void>()
    nonisolated let localDeletes = ControlledEndpoint<Void>()
    nonisolated let remoteDeletes = ControlledEndpoint<Void>()
    nonisolated let encryptionKeys = ControlledEndpoint<String?>()
    nonisolated let locals = ImmediateEndpoint<[Components.Schemas.BackupJob]>(.success([]))
    nonisolated let remotes = ImmediateEndpoint<Components.Schemas.RemoteBackupListResponse>(
        .success(.init(backups: [], status: .available, asOf: BackupFixtures.baseDate)))

    nonisolated func config() async throws -> Components.Schemas.BackupConfig {
        try await configs.call()
    }
    nonisolated func updateConfig(_ update: BackupConfigDraft) async throws
        -> Components.Schemas.BackupConfig
    { try await updates.call(update) }
    nonisolated func recoveryStatus() async throws -> Components.Schemas.BackupRecoveryStatus {
        try await statuses.call()
    }
    nonisolated func checkConnection(_ draft: BackupConfigDraft) async throws
        -> Components.Schemas.BackupDestinationCheckResponse
    {
        try await checks.call()
    }
    nonisolated func backupNow() async throws -> Components.Schemas.BackupJob {
        try await backupJobs.call()
    }
    nonisolated func localBackups() async throws -> [Components.Schemas.BackupJob] {
        try await locals.call()
    }
    nonisolated func restoreLocal(id: String) async throws { try await localRestores.call() }
    nonisolated func remoteBackups() async throws
        -> Components.Schemas.RemoteBackupListResponse
    { try await remotes.call() }
    nonisolated func restoreRemote(filename: String) async throws {}
    nonisolated func deleteLocal(id: String) async throws { try await localDeletes.call() }
    nonisolated func deleteRemote(filename: String) async throws { try await remoteDeletes.call() }
    nonisolated func encryptionKey() async throws -> String? { try await encryptionKeys.call() }
    nonisolated func householdKeyStatus() async throws -> Components.Schemas.HouseholdKeyStatus {
        throw APIError.server(501)
    }
    nonisolated func generateRecoveryKey() async throws -> Components.Schemas.RecoveryKey {
        throw APIError.server(501)
    }
    nonisolated func setSealMode(_ mode: Components.Schemas.SealModeRequest.ModePayload)
        async throws -> Components.Schemas.HouseholdKeyStatus
    { throw APIError.server(501) }
    nonisolated func unlockWithRecoveryKey(_ key: String) async throws
        -> Components.Schemas.HouseholdKeyStatus
    { throw APIError.server(501) }
    nonisolated func serverVersion() async -> String? { "0.160" }
    nonisolated func exportData() async throws -> Data { Data() }
}

private enum BackupFixtures {
    static let baseDate = Date(timeIntervalSince1970: 1_788_873_600)

    static func policy(
        _ mode: Components.Schemas.BackupRetentionPolicy.ModePayload = .tiered,
        all: Int = 3, daily: Int = 14, weekly: Int = 90,
        target: Date? = baseDate.addingTimeInterval(-90 * 86_400)
    ) -> Components.Schemas.BackupRetentionPolicy {
        .init(
            mode: mode,
            keepAllDays: mode == .tiered ? all : nil,
            dailyUntilDays: mode == .tiered ? daily : nil,
            weeklyUntilDays: mode == .tiered ? weekly : nil,
            targetOldestAt: mode == .tiered ? target : nil)
    }

    static func config(
        revision: TimeInterval = 0,
        local: Components.Schemas.BackupRetentionPolicy = policy(),
        offbox: Components.Schemas.BackupRetentionPolicy = policy(all: 7, daily: 30, weekly: 180),
        localMax: Int64? = 20_000_000_000,
        offboxMax: Int64? = 80_000_000_000,
        localReserve: Int64 = 2_000_000_000,
        offboxReserve: Int64 = 5_000_000_000,
        review: Bool = true,
        localPending: Int? = 4,
        offboxPending: Int? = 2,
        host: String = "nas.local"
    ) -> Components.Schemas.BackupConfig {
        .init(
            frequency: .every6h,
            smbHost: host,
            smbShare: "backups",
            smbFolder: "family-cfo",
            smbUsername: "backup-user",
            smbDomain: "HOME",
            hasPassword: true,
            localRetention: local,
            offboxRetention: offbox,
            localMaxBytes: localMax,
            offboxMaxBytes: offboxMax,
            localMinFreeBytes: localReserve,
            offboxMinFreeBytes: offboxReserve,
            legacyConflictDetected: false,
            retentionReviewRequired: review,
            retentionActivatedAt: review ? nil : baseDate,
            updatedAt: baseDate.addingTimeInterval(revision),
            localPendingPruneCount: localPending,
            localPendingPruneBytes: localPending.map { Int64($0) * 1_000_000_000 },
            offboxPendingPruneCount: offboxPending,
            offboxPendingPruneBytes: offboxPending.map { Int64($0) * 1_000_000_000 })
    }

    static func capacity(
        _ status: Components.Schemas.BackupCapacityObservation.StatusPayload = .ok
    ) -> Components.Schemas.BackupCapacityObservation {
        .init(
            status: status,
            totalBytes: status == .unknown ? nil : 100_000_000_000,
            availableBytes: status == .unknown ? nil : 42_000_000_000,
            reserveBytes: 1_000_000_000,
            estimatedNextBackupBytes: 1_200_000_000,
            canAcceptEstimatedBackup: status == .ok,
            asOf: baseDate,
            reasonCode: status.rawValue)
    }

    static func destination(
        _ destination: Components.Schemas.BackupDestinationRecoveryStatus.DestinationPayload,
        state: Components.Schemas.BackupDestinationRecoveryStatus.StatusPayload = .healthy,
        coverage: Components.Schemas.BackupDestinationRecoveryStatus.CoverageStatusPayload = .met,
        probe: Components.Schemas.BackupDestinationRecoveryStatus.ProbeStatusPayload = .complete,
        capacityStatus: Components.Schemas.BackupCapacityObservation.StatusPayload = .ok,
        timestampSource: Components.Schemas.BackupDestinationRecoveryStatus.OldestTimestampSourcePayload? = .jobStartedAt
    ) -> Components.Schemas.BackupDestinationRecoveryStatus {
        .init(
            destination: destination,
            configured: state != .notConfigured,
            status: state,
            coverageStatus: coverage,
            policy: policy(),
            retentionReviewRequired: false,
            retentionActivatedAt: baseDate,
            visibleArchiveCount: 12,
            readableArchiveCount: probe == .complete ? 12 : nil,
            probeStatus: probe,
            probedArchiveCount: probe == .complete ? 12 : 4,
            oldestReadableAt: probe == .unavailable ? nil : baseDate.addingTimeInterval(-20 * 86_400),
            newestReadableAt: probe == .unavailable ? nil : baseDate,
            oldestTimestampSource: timestampSource,
            metadataMismatchCount: 0,
            protectedAnomalyCount: 0,
            compatibilityUnknownCount: 0,
            knownIncompatibleCount: 0,
            capacity: capacity(capacityStatus),
            reasonCodes: [],
            asOf: baseDate,
            verificationScope: .inventoryReadProbe)
    }

    static func recovery(
        overall: Components.Schemas.BackupRecoveryStatus.OverallStatusPayload = .healthy,
        local: Components.Schemas.BackupDestinationRecoveryStatus? = nil,
        offbox: Components.Schemas.BackupDestinationRecoveryStatus? = nil
    ) -> Components.Schemas.BackupRecoveryStatus {
        .init(
            asOf: baseDate,
            overallStatus: overall,
            overallOldestReadableAt: baseDate.addingTimeInterval(-20 * 86_400),
            overallNewestReadableAt: baseDate,
            local: local ?? destination(.local),
            offbox: offbox ?? destination(.offbox),
            verificationScope: .inventoryReadProbe)
    }

    static func job(
        status: Components.Schemas.BackupJob.StatusPayload = .completed,
        errorMessage: String? = nil
    ) -> Components.Schemas.BackupJob {
        .init(
            id: "job-1", status: status, sizeBytes: 1_200_000_000,
            errorMessage: errorMessage, startedAt: baseDate, completedAt: baseDate,
            createdAt: baseDate, remoteStatus: "synced", appVersion: "0.160")
    }

    static func check() -> Components.Schemas.BackupDestinationCheckResponse {
        .init(writable: true, capacity: capacity())
    }

    static func remote() -> Components.Schemas.RemoteBackup {
        .init(
            filename: "family-cfo-test.enc", sizeBytes: 1_200_000_000,
            modifiedAt: Int64(baseDate.timeIntervalSince1970), appVersion: "0.160")
    }
}

@MainActor
struct BackupViewModelRetentionTests {
    private func loaded(
        config: Components.Schemas.BackupConfig = BackupFixtures.config(),
        status: Components.Schemas.BackupRecoveryStatus = BackupFixtures.recovery(),
        session: @escaping @MainActor () -> String? = { "session-a" }
    ) async -> (BackupViewModel, MockRetentionBackupAPI) {
        let api = MockRetentionBackupAPI()
        await api.configs.enqueue(.success(config))
        await api.statuses.enqueue(.success(status))
        let viewModel = BackupViewModel(api: api, sessionIdentity: session)
        await viewModel.load()
        return (viewModel, api)
    }

    @Test func loadMapsIndependentPoliciesCapsReservesAndPendingPreview() async {
        let (viewModel, _) = await loaded()

        #expect(viewModel.localRetention.keepAllDays == 3)
        #expect(viewModel.localRetention.maxGB == 20)
        #expect(viewModel.localRetention.reserveGB == 2)
        #expect(viewModel.offboxRetention.keepAllDays == 7)
        #expect(viewModel.offboxRetention.dailyUntilDays == 30)
        #expect(viewModel.offboxRetention.weeklyUntilDays == 180)
        #expect(viewModel.offboxRetention.maxGB == 80)
        #expect(viewModel.offboxRetention.reserveGB == 5)
        #expect(viewModel.retentionReviewRequired)
        #expect(viewModel.localPendingPruneCount == 4)
        #expect(viewModel.offboxPendingPruneCount == 2)
        #expect(viewModel.recoveryStatus?.overallStatus == .healthy)
    }

    @Test func validationIsIndependentOrderedAndBounded() async {
        let (viewModel, _) = await loaded()
        viewModel.localRetention.keepAllDays = 0
        #expect(viewModel.retentionValidationMessage?.contains("On this box") == true)

        viewModel.localRetention.keepAllDays = 3
        viewModel.offboxRetention.dailyUntilDays = 2
        #expect(viewModel.retentionValidationMessage?.contains("Synology") == true)

        viewModel.offboxRetention.mode = .keepAll
        #expect(viewModel.retentionValidationMessage == nil)
    }

    @Test func retentionWaitsForExplicitConfirmedCasSave() async {
        let (viewModel, api) = await loaded()
        viewModel.localRetention.weeklyUntilDays = 365
        viewModel.offboxRetention.maxGB = 120
        await api.updates.enqueue(.success(BackupFixtures.config(revision: 1, review: false)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery()))

        // Editing alone is a draft and causes no request.
        #expect(await api.updates.drafts().isEmpty)
        await viewModel.saveAndActivateRetention()

        let sent = await api.updates.drafts()
        #expect(sent.count == 1)
        #expect(sent[0].confirmRetentionPolicy)
        #expect(sent[0].expectedUpdatedAt == BackupFixtures.baseDate)
        #expect(sent[0].localRetention.weeklyUntilDays == 365)
        #expect(sent[0].offboxRetention.maxGB == 120)
        // The returned representation is authoritative and must become both the
        // visible value and comparison baseline when the user did not edit again.
        #expect(viewModel.localRetention.weeklyUntilDays == 90)
        #expect(viewModel.offboxRetention.maxGB == 80)
        #expect(!viewModel.hasRetentionChanges)
        #expect(!viewModel.canActivateRetention)
    }

    @Test func conflictPreservesDraftAndLoadsCurrentServerConfigSeparately() async {
        let (viewModel, api) = await loaded()
        viewModel.localRetention.weeklyUntilDays = 365
        await api.updates.enqueue(.failure(BackupError.configurationConflict("Changed elsewhere")))
        let current = BackupFixtures.config(revision: 5, local: BackupFixtures.policy(weekly: 730))
        await api.configs.enqueue(.success(current))

        await viewModel.saveAndActivateRetention()

        #expect(viewModel.localRetention.weeklyUntilDays == 365)
        #expect(viewModel.conflictingConfig?.updatedAt == current.updatedAt)
        #expect(viewModel.configError == "Changed elsewhere")

        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await viewModel.useCurrentBoxSettings()
        #expect(viewModel.localRetention.weeklyUntilDays == 730)
        #expect(viewModel.conflictingConfig == nil)
    }

    @Test func delayedConflictFetchCannotPublishAfterANewerNormalLoad() async {
        let (viewModel, api) = await loaded()
        viewModel.localRetention.weeklyUntilDays = 365
        await api.updates.enqueue(.failure(BackupError.configurationConflict("Changed elsewhere")))

        let activation = Task { await viewModel.saveAndActivateRetention() }
        await api.configs.waitForCalls(2)

        let newest = BackupFixtures.config(revision: 8, host: "newest.local")
        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        let reload = Task { await viewModel.load() }
        await api.configs.waitForCalls(3)
        await api.configs.resolve(2, .success(newest))
        await reload.value

        // The conflict fetch began first but finishes after the normal reload.
        await api.configs.resolve(
            1, .success(BackupFixtures.config(revision: 7, host: "conflict.local")))
        await activation.value

        #expect(viewModel.host == "newest.local")
        #expect(viewModel.configUpdatedAt == newest.updatedAt)
        #expect(viewModel.conflictingConfig == nil)
        #expect(viewModel.configError == nil)
    }

    @Test func newestConfigCompletionOwnsTheScreenAndOldSuccessCannotReplaceIt() async {
        let api = MockRetentionBackupAPI()
        let viewModel = BackupViewModel(api: api)
        let old = Task { await viewModel.load() }
        await api.configs.waitForCalls(1)
        let current = Task { await viewModel.load() }
        await api.configs.waitForCalls(2)

        let newest = BackupFixtures.config(revision: 2, host: "newest.local")
        await api.configs.resolve(1, .success(newest))
        await api.statuses.waitForCalls(1)
        await api.statuses.resolve(0, .success(BackupFixtures.recovery()))
        await current.value

        await api.configs.resolve(0, .success(BackupFixtures.config(revision: 1, host: "old.local")))
        await old.value

        #expect(viewModel.host == "newest.local")
        #expect(viewModel.configUpdatedAt == newest.updatedAt)
        #expect(viewModel.configError == nil)
    }

    @Test func currentStatusFailureClearsStaleRecoveryWithoutPoisoningConfig() async {
        let (viewModel, api) = await loaded()
        #expect(viewModel.recoveryStatus != nil)
        let revision = viewModel.configUpdatedAt
        await api.statuses.enqueue(.failure(APIError.server(503)))

        await viewModel.refreshRecoveryStatus()

        #expect(viewModel.recoveryStatus == nil)
        #expect(viewModel.recoveryStatusError != nil)
        #expect(viewModel.configError == nil)
        #expect(viewModel.configUpdatedAt == revision)
        #expect(viewModel.canActivateRetention)
    }

    @Test func currentLocalListFailureClearsStaleRowsAndUsesDistinctError() async {
        let (viewModel, api) = await loaded()
        await api.locals.set(.success([BackupFixtures.job()]))
        await viewModel.loadBackups(refreshStatus: false)
        #expect(viewModel.localBackups.count == 1)
        #expect(viewModel.localListError == nil)

        await api.locals.set(.failure(APIError.server(503)))
        await viewModel.loadBackups(refreshStatus: false)

        #expect(viewModel.localBackups.isEmpty)
        #expect(viewModel.localListError != nil)
        #expect(viewModel.remoteListError == nil)
        #expect(viewModel.recoveryStatus != nil)
        #expect(viewModel.configError == nil)
    }

    @Test func remoteListFailureIsDistinctFromEmptyAndDoesNotClearRecoveryStatus() async {
        let (viewModel, api) = await loaded()
        await api.remotes.set(.failure(APIError.server(503)))

        await viewModel.loadBackups(refreshStatus: false)

        #expect(viewModel.remoteBackups.isEmpty)
        #expect(viewModel.remoteListError != nil)
        #expect(viewModel.recoveryStatus != nil)
        #expect(viewModel.recoveryStatusError == nil)
        #expect(viewModel.configError == nil)
    }

    @Test func successfulUnavailableRemoteListIsNotPresentedAsEmpty() async {
        let (viewModel, api) = await loaded()
        await api.remotes.set(
            .success(
                .init(
                    backups: [], status: .unavailable, asOf: BackupFixtures.baseDate,
                    reason: "The Synology inventory is unavailable.")))

        await viewModel.loadBackups(refreshStatus: false)

        #expect(viewModel.remoteBackups.isEmpty)
        #expect(viewModel.remoteListError == "The Synology inventory is unavailable.")
    }

    @Test func newestStatusCompletionOwnsSuccessAndOldFailureCannotClearIt() async {
        let api = MockRetentionBackupAPI()
        let viewModel = BackupViewModel(api: api)
        let first = Task { await viewModel.refreshRecoveryStatus() }
        await api.statuses.waitForCalls(1)
        let second = Task { await viewModel.refreshRecoveryStatus() }
        await api.statuses.waitForCalls(2)

        let newest = BackupFixtures.recovery(overall: .constrained)
        await api.statuses.resolve(1, .success(newest))
        await second.value
        await api.statuses.resolve(0, .failure(APIError.server(503)))
        await first.value

        #expect(viewModel.recoveryStatus?.overallStatus == .constrained)
        #expect(viewModel.recoveryStatusError == nil)
    }

    @Test func oldSessionStatusCompletionCannotSeedTheReplacementSession() async {
        let api = MockRetentionBackupAPI()
        var session = "session-a"
        let viewModel = BackupViewModel(api: api, sessionIdentity: { session })
        let old = Task { await viewModel.refreshRecoveryStatus() }
        await api.statuses.waitForCalls(1)
        session = "session-b"
        let current = Task { await viewModel.refreshRecoveryStatus() }
        await api.statuses.waitForCalls(2)

        await api.statuses.resolve(1, .success(BackupFixtures.recovery(overall: .healthy)))
        await current.value
        await api.statuses.resolve(0, .success(BackupFixtures.recovery(overall: .degraded)))
        await old.value

        #expect(viewModel.recoveryStatus?.overallStatus == .healthy)
    }

    @Test func oldSessionKeyCompletionCannotRevealThePreviousSessionsSecret() async {
        let api = MockRetentionBackupAPI()
        var session = "session-a"
        let viewModel = BackupViewModel(api: api, sessionIdentity: { session })
        let reveal = Task { await viewModel.revealKey() }
        await api.encryptionKeys.waitForCalls(1)

        session = "session-b"
        await api.encryptionKeys.resolve(0, .success("old-session-secret"))
        await reveal.value

        #expect(viewModel.revealedKey == nil)
    }

    @Test func restoreRefreshesRotatedConfigAndRecoveryState() async {
        let (viewModel, api) = await loaded()
        let refreshedConfig = BackupFixtures.config(revision: 60, review: true)
        let refreshedStatus = BackupFixtures.recovery(overall: .degraded)
        await api.localRestores.enqueue(.success(()))
        await api.configs.enqueue(.success(refreshedConfig))
        await api.statuses.enqueue(.success(refreshedStatus))

        await viewModel.restoreLocal(BackupFixtures.job())

        #expect(viewModel.configUpdatedAt == refreshedConfig.updatedAt)
        #expect(viewModel.retentionReviewRequired)
        #expect(viewModel.recoveryStatus?.overallStatus == .degraded)
    }

    @Test func differentConcurrentActionsKeepIndependentOwnersAndClearBusyState() async {
        let (viewModel, api) = await loaded()

        let backup = Task { await viewModel.backupNow() }
        await api.backupJobs.waitForCalls(1)
        let restore = Task { await viewModel.restoreLocal(BackupFixtures.job()) }
        await api.localRestores.waitForCalls(1)
        let check = Task { await viewModel.testConnection() }
        await api.checks.waitForCalls(1)

        #expect(viewModel.isBackingUp)
        #expect(viewModel.isRestoring)
        #expect(viewModel.isChecking)

        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await api.checks.resolve(0, .success(BackupFixtures.check()))
        await check.value
        #expect(!viewModel.isChecking)
        #expect(viewModel.isBackingUp)
        #expect(viewModel.isRestoring)

        await api.configs.enqueue(.success(BackupFixtures.config(revision: 1)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await api.localRestores.resolve(0, .success(()))
        await restore.value
        #expect(!viewModel.isRestoring)
        #expect(viewModel.isBackingUp)

        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await api.backupJobs.resolve(0, .success(BackupFixtures.job()))
        await backup.value
        #expect(!viewModel.isBackingUp)
    }

    @Test func staleSessionMutationDoesNotPublishButStillCleansItsBusySlot() async {
        let api = MockRetentionBackupAPI()
        var session = "session-a"
        let viewModel = BackupViewModel(api: api, sessionIdentity: { session })
        let check = Task { await viewModel.testConnection() }
        await api.checks.waitForCalls(1)
        session = "session-b"

        await api.checks.resolve(0, .success(BackupFixtures.check()))
        await check.value

        #expect(viewModel.checkResult == nil)
        #expect(!viewModel.isChecking)
    }

    @Test func autosavesAreSerializedAndCoalesceToTheNewestDraft() async {
        let (viewModel, api) = await loaded()
        viewModel.host = "nas-one.local"
        let first = Task { await viewModel.save() }
        await api.updates.waitForCalls(1)

        viewModel.share = "new-share"
        let second = Task { await viewModel.save() }
        await second.value
        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await api.updates.resolve(0, .success(BackupFixtures.config(revision: 1, host: "nas-one.local")))
        await api.updates.waitForCalls(2)

        await api.statuses.enqueue(.success(BackupFixtures.recovery()))
        await api.updates.resolve(1, .success(BackupFixtures.config(revision: 2, host: "nas-one.local")))
        await first.value

        let drafts = await api.updates.drafts()
        #expect(drafts.count == 2)
        #expect(drafts[0].host == "nas-one.local")
        #expect(drafts[0].share == "backups")
        #expect(drafts[1].share == "new-share")
        #expect(drafts[1].expectedUpdatedAt == BackupFixtures.baseDate.addingTimeInterval(1))
        #expect(await api.updates.maxConcurrent() == 1)
        #expect(!drafts[0].confirmRetentionPolicy)
    }

    @Test func backupCreationRefreshesListsAndRecoveryStatus() async {
        let (viewModel, api) = await loaded()
        await api.backupJobs.enqueue(.success(BackupFixtures.job()))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .degraded)))
        let before = await api.statuses.callCount()

        await viewModel.backupNow()

        #expect(await api.statuses.callCount() == before + 1)
        #expect(viewModel.recoveryStatus?.overallStatus == .degraded)
    }

    @Test func failedMutationAttemptsStillRefreshRecoveryStatus() async {
        let (viewModel, api) = await loaded()
        let before = await api.statuses.callCount()

        await api.checks.enqueue(.failure(APIError.server(502)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .degraded)))
        await viewModel.testConnection()
        #expect(await api.statuses.callCount() == before + 1)
        #expect(!viewModel.isChecking)

        await api.backupJobs.enqueue(.failure(APIError.server(503)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .constrained)))
        await viewModel.backupNow()
        #expect(await api.statuses.callCount() == before + 2)
        #expect(!viewModel.isBackingUp)

        await api.localDeletes.enqueue(.failure(APIError.server(409)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .degraded)))
        await viewModel.deleteLocal(BackupFixtures.job())
        #expect(await api.statuses.callCount() == before + 3)

        await api.remoteDeletes.enqueue(.failure(APIError.server(409)))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .constrained)))
        await viewModel.deleteRemote(BackupFixtures.remote())
        #expect(await api.statuses.callCount() == before + 4)
        #expect(viewModel.recoveryStatus?.overallStatus == .constrained)
    }

    @Test func failedBackupJobNeverAnnouncesCompletionAndStillRefreshesStatus() async {
        let (viewModel, api) = await loaded()
        await api.backupJobs.enqueue(
            .success(BackupFixtures.job(status: .failed, errorMessage: "Disk is full")))
        await api.statuses.enqueue(.success(BackupFixtures.recovery(overall: .constrained)))

        await viewModel.backupNow()

        #expect(viewModel.latest?.status == .failed)
        #expect(viewModel.statusMessage == nil)
        #expect(viewModel.errorMessage?.contains("Disk is full") == true)
        #expect(viewModel.recoveryStatus?.overallStatus == .constrained)
    }

    @Test func remoteDeleteTreatsUnwritableOkResponseAsFailure() async {
        let response = Components.Schemas.BackupDestinationCheckResponse(
            writable: false,
            reason: "Share is unavailable",
            capacity: BackupFixtures.capacity(.unknown))
        let transport = RemoteDeleteTransport(response: response)
        let client = Client(
            serverURL: URL(string: "https://box.local")!, transport: transport)
        let api = LiveBackupAPI(client: client)

        do {
            try await api.deleteRemote(filename: "backup.enc")
            Issue.record("expected an unwritable delete response to throw")
        } catch {
            #expect(error.localizedDescription.contains("Share is unavailable"))
        }
    }

    @Test func recoveryCopyKeepsStatesAndCapacityClaimsDistinct() {
        let building = BackupFixtures.destination(.local, coverage: .building)
        let shortened = BackupFixtures.destination(.local, state: .constrained, coverage: .shortened)
        let unavailable = BackupFixtures.destination(.offbox, state: .unavailable, probe: .unavailable)
        let notConfigured = BackupFixtures.destination(.offbox, state: .notConfigured, coverage: .notApplicable)

        #expect(BackupViewModel.destinationStateMessage(building)?.contains("building") == true)
        #expect(BackupViewModel.destinationStateMessage(shortened)?.contains("shortened") == true)
        #expect(BackupViewModel.destinationStateMessage(unavailable)?.contains("unavailable") == true)
        #expect(BackupViewModel.destinationStateMessage(notConfigured)?.contains("configured") == true)
        #expect(BackupViewModel.capacityMessage(BackupFixtures.capacity(.unknown)).contains("still be attempted"))
        #expect(BackupViewModel.probeTitle(.partial) != BackupViewModel.probeTitle(.unavailable))
        #expect(BackupSettingsView.dayTimeLabel(1_788_873_600).isEmpty == false)
    }
}
