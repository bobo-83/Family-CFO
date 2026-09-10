import Foundation

/// Drives the box-global backup settings screen. Destination and cadence edits
/// remain convenient autosaves; retention and capacity are an explicit,
/// optimistic, destructive-policy activation (ADR 0077).
@MainActor
@Observable
final class BackupViewModel {
    typealias SessionIdentity = @MainActor () -> String?

    private let api: BackupAPI
    private let sessionIdentity: SessionIdentity

    var host = ""
    var share = ""
    var folder = ""
    var username = ""
    var password = ""
    var domain = ""
    var frequency: Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload = .daily
    var localRetention = BackupRetentionDraft()
    var offboxRetention = BackupRetentionDraft()

    private(set) var hasStoredPassword = false
    private(set) var passwordEdited = false
    private(set) var revealedKey: String?
    private(set) var keyStatus: Components.Schemas.HouseholdKeyStatus?
    private(set) var generatedRecoveryKey: String?
    private(set) var latest: Components.Schemas.BackupJob?
    private(set) var localBackups: [Components.Schemas.BackupJob] = []
    private(set) var remoteBackups: [Components.Schemas.RemoteBackup] = []
    private(set) var runningVersion: String?

    private(set) var recoveryStatus: Components.Schemas.BackupRecoveryStatus?
    private(set) var recoveryStatusError: String?
    private(set) var localListError: String?
    private(set) var remoteListError: String?
    private(set) var configError: String?
    private(set) var conflictingConfig: Components.Schemas.BackupConfig?
    private(set) var configUpdatedAt: Date?
    private(set) var retentionReviewRequired = false
    private(set) var retentionActivatedAt: Date?
    private(set) var localPendingPruneCount: Int?
    private(set) var localPendingPruneBytes: Int64?
    private(set) var offboxPendingPruneCount: Int?
    private(set) var offboxPendingPruneBytes: Int64?
    private(set) var legacyConflictDetected = false

    private(set) var isLoading = false
    private(set) var isSaving = false
    private(set) var isActivatingRetention = false
    private(set) var isBackingUp = false
    private(set) var isRestoring = false
    private(set) var isChecking = false
    private(set) var checkResult: Components.Schemas.BackupDestinationCheckResponse?
    var statusMessage: String?
    var errorMessage: String?

    private var configGeneration: UInt64 = 0
    private var statusGeneration: UInt64 = 0
    private var listGeneration: UInt64 = 0
    private var checkGeneration: UInt64 = 0
    private var backupGeneration: UInt64 = 0
    private var restoreGeneration: UInt64 = 0
    private var localDeleteGeneration: UInt64 = 0
    private var remoteDeleteGeneration: UInt64 = 0
    private var conflictGeneration: UInt64 = 0

    private struct OperationalDraft: Equatable, Sendable {
        var frequency: Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload
        var host: String
        var share: String
        var folder: String
        var username: String
        var password: String?
        var domain: String

        func withoutPassword() -> Self {
            var copy = self
            copy.password = nil
            return copy
        }
    }

    private struct ActivationDraft: Equatable, Sendable {
        var operational: OperationalDraft
        var local: BackupRetentionDraft
        var offbox: BackupRetentionDraft
    }

    private enum SaveKind: Sendable {
        case operational(OperationalDraft)
        case activation(ActivationDraft)
    }

    private struct RequestOwner: Equatable, Sendable {
        let session: String
        let generation: UInt64
        let configToken: Date?
    }

    private var serverOperational: OperationalDraft?
    private var serverLocalRetention: BackupRetentionDraft?
    private var serverOffboxRetention: BackupRetentionDraft?
    private var pendingSave: SaveKind?

    init(api: BackupAPI, sessionIdentity: @escaping SessionIdentity = { "standalone" }) {
        self.api = api
        self.sessionIdentity = sessionIdentity
    }

    private var operationalDraft: OperationalDraft {
        .init(
            frequency: frequency,
            host: host,
            share: share,
            folder: folder,
            username: username,
            password: passwordEdited ? password : nil,
            domain: domain)
    }

    var retentionValidationMessage: String? {
        Self.validationMessage(for: localRetention, destination: String(localized: "On this box"))
            ?? Self.validationMessage(for: offboxRetention, destination: String(localized: "Synology"))
    }

    var hasRetentionChanges: Bool {
        localRetention != serverLocalRetention || offboxRetention != serverOffboxRetention
    }

    var canActivateRetention: Bool {
        configUpdatedAt != nil && retentionValidationMessage == nil && !isSaving
            && (hasRetentionChanges || retentionReviewRequired)
    }

    var latestSummary: String? {
        guard let latest else { return nil }
        if latest.status == .failed {
            return latest.errorMessage.map { String(localized: "Last backup failed: \($0)") }
                ?? String(localized: "Last backup failed.")
        }
        var parts: [String] = []
        if let when = latest.completedAt {
            parts.append(when.formatted(date: .abbreviated, time: .shortened))
        }
        if let size = latest.sizeBytes {
            parts.append(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
        }
        return parts.isEmpty ? String(localized: "Completed") : parts.joined(separator: " · ")
    }

    var remoteWarning: String? {
        guard let latest, !host.isEmpty else { return nil }
        if latest.remoteStatus == "failed" {
            return latest.remoteError.map { String(localized: "Last copy to Synology failed: \($0)") }
                ?? String(localized: "Last copy to Synology failed.")
        }
        return nil
    }

    var remoteSynced: Bool { latest?.remoteStatus == "synced" }

    var canTest: Bool {
        !host.trimmingCharacters(in: .whitespaces).isEmpty
            && !share.trimmingCharacters(in: .whitespaces).isEmpty
            && !username.trimmingCharacters(in: .whitespaces).isEmpty
            && (passwordEdited || hasStoredPassword)
    }

    func load() async {
        guard let session = sessionIdentity() else { return }
        invalidateConflictFetch()
        configGeneration &+= 1
        let owner = RequestOwner(session: session, generation: configGeneration, configToken: configUpdatedAt)
        isLoading = true
        do {
            let config = try await api.config()
            guard ownsConfig(owner, requireToken: false) else {
                if configGeneration == owner.generation { isLoading = false }
                return
            }
            applyFullConfig(config)
            configError = nil
            conflictingConfig = nil
            isLoading = false
            await loadBackups(refreshStatus: false)
            await refreshRecoveryStatus()
        } catch {
            guard ownsConfig(owner, requireToken: false) else {
                if configGeneration == owner.generation { isLoading = false }
                return
            }
            isLoading = false
            configError = ChatViewModel.describe(error)
        }
    }

    /// Lists and key posture are useful even when recovery status is unavailable.
    func loadBackups(refreshStatus: Bool = true) async {
        guard let session = sessionIdentity() else { return }
        listGeneration &+= 1
        let generation = listGeneration
        let configuredRemote = !host.trimmingCharacters(in: .whitespaces).isEmpty

        async let version = api.serverVersion()
        async let local = Self.capture { try await self.api.localBackups() }
        async let keys = Self.capture { try await self.api.householdKeyStatus() }
        let remote: Result<[Components.Schemas.RemoteBackup], Error> = configuredRemote
            ? await Self.capture { try await self.api.remoteBackups() }
            : .success([])
        let loadedVersion = await version
        let loadedLocal = await local
        let loadedKeys = await keys

        guard sessionIdentity() == session, listGeneration == generation, !Task.isCancelled else { return }
        runningVersion = loadedVersion
        switch loadedLocal {
        case .success(let backups):
            localBackups = backups
            localListError = nil
        case .failure(let error):
            // A current failure must not leave an older inventory looking current.
            localBackups = []
            localListError = ChatViewModel.describe(error)
        }
        if case .success(let status) = loadedKeys { keyStatus = status }
        switch remote {
        case .success(let backups):
            remoteBackups = backups
            remoteListError = nil
        case .failure(let error):
            remoteBackups = []
            remoteListError = ChatViewModel.describe(error)
        }
        if refreshStatus { await refreshRecoveryStatus() }
    }

    /// Numeric dotted-tuple compare. A backup made by a newer app is not restorable
    /// until the box is updated.
    func isFromNewerVersion(_ appVersion: String?) -> Bool {
        guard let appVersion, let runningVersion else { return false }
        let backup = appVersion.split(separator: ".").map { Int($0) ?? 0 }
        let running = runningVersion.split(separator: ".").map { Int($0) ?? 0 }
        for index in 0..<max(backup.count, running.count) {
            let backupPart = index < backup.count ? backup[index] : 0
            let runningPart = index < running.count ? running[index] : 0
            if backupPart != runningPart { return backupPart > runningPart }
        }
        return false
    }

    func refreshRecoveryStatus() async {
        guard let session = sessionIdentity() else { return }
        statusGeneration &+= 1
        let owner = RequestOwner(
            session: session, generation: statusGeneration, configToken: configUpdatedAt)
        do {
            let status = try await api.recoveryStatus()
            guard ownsStatus(owner) else { return }
            recoveryStatus = status
            recoveryStatusError = nil
        } catch {
            guard ownsStatus(owner) else { return }
            // A current failure must not leave old dates looking current.
            recoveryStatus = nil
            recoveryStatusError = ChatViewModel.describe(error)
        }
    }

    /// Destination/cadence autosave. Concurrent calls collapse to the newest
    /// pending snapshot and share the same serialized CAS lane as activation.
    func save() async {
        let snapshot = operationalDraft
        if snapshot.withoutPassword() == serverOperational, snapshot.password == nil { return }
        if case .activation(var activation) = pendingSave {
            // Preserve the explicit retention snapshot while coalescing newer
            // destination/cadence edits into that not-yet-started save.
            activation.operational = snapshot
            pendingSave = .activation(activation)
        } else {
            pendingSave = .operational(snapshot)
        }
        await drainSaveQueue()
    }

    func saveAndActivateRetention() async {
        guard retentionValidationMessage == nil else { return }
        pendingSave = .activation(
            .init(operational: operationalDraft, local: localRetention, offbox: offboxRetention))
        await drainSaveQueue()
    }

    private func drainSaveQueue() async {
        guard !isSaving else { return }
        isSaving = true
        defer {
            isSaving = false
            isActivatingRetention = false
        }
        while let next = pendingSave {
            pendingSave = nil
            let shouldContinue = await performSave(next)
            if !shouldContinue { pendingSave = nil; return }
        }
    }

    private func performSave(_ kind: SaveKind) async -> Bool {
        guard let session = sessionIdentity(), let token = configUpdatedAt else {
            configError = String(localized: "Backup settings are still loading.")
            return false
        }

        let activation: ActivationDraft?
        let operational: OperationalDraft
        switch kind {
        case .operational(let draft):
            if draft.withoutPassword() == serverOperational, draft.password == nil { return true }
            activation = nil
            operational = draft
            isActivatingRetention = false
        case .activation(let draft):
            activation = draft
            operational = draft.operational
            isActivatingRetention = true
        }

        configGeneration &+= 1
        let owner = RequestOwner(session: session, generation: configGeneration, configToken: token)
        let request = BackupConfigDraft(
            frequency: operational.frequency,
            host: operational.host,
            share: operational.share,
            folder: operational.folder,
            username: operational.username,
            password: operational.password,
            domain: operational.domain,
            localRetention: activation?.local ?? localRetention,
            offboxRetention: activation?.offbox ?? offboxRetention,
            expectedUpdatedAt: token,
            confirmRetentionPolicy: activation != nil)

        do {
            let config = try await api.updateConfig(request)
            guard ownsConfig(owner, requireToken: true) else { return false }
            configUpdatedAt = config.updatedAt
            latest = config.latest
            hasStoredPassword = config.hasPassword
            serverOperational = operational.withoutPassword()
            if let sentPassword = operational.password, passwordEdited, password == sentPassword {
                password = ""
                passwordEdited = false
            }
            updateConfigMetadata(config)
            if let activation {
                let returnedLocal = Self.retentionDraft(
                    config.localRetention, maxBytes: config.localMaxBytes,
                    reserveBytes: config.localMinFreeBytes)
                let returnedOffbox = Self.retentionDraft(
                    config.offboxRetention, maxBytes: config.offboxMaxBytes,
                    reserveBytes: config.offboxMinFreeBytes)
                if localRetention == activation.local { localRetention = returnedLocal }
                if offboxRetention == activation.offbox { offboxRetention = returnedOffbox }
                serverLocalRetention = returnedLocal
                serverOffboxRetention = returnedOffbox
            }
            configError = nil
            invalidateConflictFetch()
            await refreshRecoveryStatus()
            return true
        } catch let conflict as BackupError {
            guard ownsConfig(owner, requireToken: true) else { return false }
            if case .configurationConflict(let message) = conflict {
                configError = message
                await loadConflictingConfig(for: owner)
                return false
            }
            configError = ChatViewModel.describe(conflict)
            return false
        } catch {
            guard ownsConfig(owner, requireToken: true) else { return false }
            configError = ChatViewModel.describe(error)
            return false
        }
    }

    private func loadConflictingConfig(for configOwner: RequestOwner) async {
        conflictGeneration &+= 1
        let generation = conflictGeneration
        do {
            let current = try await api.config()
            guard ownsConflictFetch(
                configOwner: configOwner, conflictGeneration: generation)
            else { return }
            // Deliberately separate: never replace the administrator's unsaved draft.
            conflictingConfig = current
        } catch {
            guard ownsConflictFetch(
                configOwner: configOwner, conflictGeneration: generation)
            else { return }
            // Keep the original conflict message; reconciliation remains explicit.
        }
    }

    func useCurrentBoxSettings() async {
        guard let config = conflictingConfig else { return }
        applyFullConfig(config)
        conflictingConfig = nil
        configError = nil
        await refreshRecoveryStatus()
    }

    func passwordChanged() { passwordEdited = true }

    func testConnection() async {
        guard !isChecking, let session = sessionIdentity() else { return }
        checkGeneration &+= 1
        let generation = checkGeneration
        isChecking = true
        defer {
            if ownsOperationSlot(generation, current: checkGeneration) {
                isChecking = false
            }
        }
        let snapshot = operationalDraft
        let request = BackupConfigDraft(
            frequency: snapshot.frequency, host: snapshot.host, share: snapshot.share,
            folder: snapshot.folder, username: snapshot.username, password: snapshot.password,
            domain: snapshot.domain, localRetention: localRetention,
            offboxRetention: offboxRetention, expectedUpdatedAt: configUpdatedAt,
            confirmRetentionPolicy: false)
        do {
            let result = try await api.checkConnection(request)
            guard ownsOperation(session, generation, current: checkGeneration) else { return }
            checkResult = result
            errorMessage = nil
        } catch {
            guard ownsOperation(session, generation, current: checkGeneration) else { return }
            checkResult = nil
            errorMessage = ChatViewModel.describe(error)
        }
        // Even a failed or partially successful probe can change observed capacity.
        guard ownsOperation(session, generation, current: checkGeneration) else { return }
        await refreshRecoveryStatus()
    }

    func backupNow() async {
        guard !isBackingUp, let session = sessionIdentity() else { return }
        backupGeneration &+= 1
        let generation = backupGeneration
        isBackingUp = true
        defer {
            if ownsOperationSlot(generation, current: backupGeneration) {
                isBackingUp = false
            }
        }
        do {
            let job = try await api.backupNow()
            guard ownsOperation(session, generation, current: backupGeneration) else { return }
            latest = job
            if job.status == .completed {
                statusMessage = String(localized: "Backup complete.")
                errorMessage = nil
            } else if job.status == .failed {
                statusMessage = nil
                errorMessage = job.errorMessage.map {
                    String(localized: "Last backup failed: \($0)")
                } ?? String(localized: "Last backup failed.")
            } else {
                // The endpoint normally completes synchronously. If a future
                // server returns an in-progress job, do not claim completion.
                statusMessage = nil
                errorMessage = nil
            }
            await loadBackups(refreshStatus: false)
        } catch {
            guard ownsOperation(session, generation, current: backupGeneration) else { return }
            errorMessage = ChatViewModel.describe(error)
        }
        // A thrown request and a returned failed job can both change recovery state.
        guard ownsOperation(session, generation, current: backupGeneration) else { return }
        await refreshRecoveryStatus()
    }

    func loadRemote() async {
        await loadBackups(refreshStatus: true)
    }

    func restoreLocal(_ backup: Components.Schemas.BackupJob) async {
        guard !isRestoring, let session = sessionIdentity() else { return }
        restoreGeneration &+= 1
        let generation = restoreGeneration
        isRestoring = true
        defer {
            if ownsOperationSlot(generation, current: restoreGeneration) {
                isRestoring = false
            }
        }
        do {
            try await api.restoreLocal(id: backup.id)
            guard ownsOperation(session, generation, current: restoreGeneration) else { return }
            statusMessage = String(localized: "Restored. Reopen the app to see restored data.")
            errorMessage = nil
        } catch {
            guard ownsOperation(session, generation, current: restoreGeneration) else { return }
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func revealKey() async {
        do {
            revealedKey = try await api.encryptionKey()
            errorMessage = nil
        } catch { errorMessage = ChatViewModel.describe(error) }
    }

    func createRecoveryKey() async {
        do {
            generatedRecoveryKey = try await api.generateRecoveryKey().recoveryKey
            keyStatus = try? await api.householdKeyStatus()
            errorMessage = nil
        } catch { errorMessage = ChatViewModel.describe(error) }
    }

    func setSealMode(sealed: Bool) async {
        do {
            keyStatus = try await api.setSealMode(sealed ? .sealed : .convenient)
            errorMessage = nil
        } catch { errorMessage = ChatViewModel.describe(error) }
    }

    func unlockWithRecoveryKey(_ key: String) async {
        let trimmed = key.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        do {
            keyStatus = try await api.unlockWithRecoveryKey(trimmed)
            statusMessage = String(localized: "Household unlocked.")
            errorMessage = nil
        } catch { errorMessage = ChatViewModel.describe(error) }
    }

    private(set) var isExporting = false
    var exportedFileURL: URL?

    func exportData() async {
        guard !isExporting else { return }
        isExporting = true
        exportedFileURL = nil
        defer { isExporting = false }
        do {
            let data = try await api.exportData()
            let day = Date().formatted(.iso8601.year().month().day().dateSeparator(.dash))
            let url = FileManager.default.temporaryDirectory
                .appendingPathComponent("family-cfo-export-\(day).zip")
            try data.write(to: url, options: .atomic)
            exportedFileURL = url
            errorMessage = nil
        } catch { errorMessage = ChatViewModel.describe(error) }
    }

    func deleteLocal(_ backup: Components.Schemas.BackupJob) async {
        guard let session = sessionIdentity() else { return }
        localDeleteGeneration &+= 1
        let generation = localDeleteGeneration
        do {
            try await api.deleteLocal(id: backup.id)
            guard ownsOperation(session, generation, current: localDeleteGeneration) else { return }
            await loadBackups(refreshStatus: false)
        } catch {
            guard ownsOperation(session, generation, current: localDeleteGeneration) else { return }
            errorMessage = ChatViewModel.describe(error)
        }
        guard ownsOperation(session, generation, current: localDeleteGeneration) else { return }
        await refreshRecoveryStatus()
    }

    func deleteRemote(_ backup: Components.Schemas.RemoteBackup) async {
        guard let session = sessionIdentity() else { return }
        remoteDeleteGeneration &+= 1
        let generation = remoteDeleteGeneration
        do {
            try await api.deleteRemote(filename: backup.filename)
            guard ownsOperation(session, generation, current: remoteDeleteGeneration) else { return }
            await loadBackups(refreshStatus: false)
        } catch {
            guard ownsOperation(session, generation, current: remoteDeleteGeneration) else { return }
            errorMessage = ChatViewModel.describe(error)
        }
        guard ownsOperation(session, generation, current: remoteDeleteGeneration) else { return }
        await refreshRecoveryStatus()
    }

    func restore(_ backup: Components.Schemas.RemoteBackup) async {
        guard !isRestoring, let session = sessionIdentity() else { return }
        restoreGeneration &+= 1
        let generation = restoreGeneration
        isRestoring = true
        defer {
            if ownsOperationSlot(generation, current: restoreGeneration) {
                isRestoring = false
            }
        }
        do {
            try await api.restoreRemote(filename: backup.filename)
            guard ownsOperation(session, generation, current: restoreGeneration) else { return }
            statusMessage = String(
                localized: "Restored from \(backup.filename). Reopen the app to see restored data.")
            errorMessage = nil
        } catch {
            guard ownsOperation(session, generation, current: restoreGeneration) else { return }
            errorMessage = ChatViewModel.describe(error)
        }
    }

    private nonisolated static func capture<T: Sendable>(
        _ operation: @escaping @Sendable () async throws -> T
    ) async -> Result<T, Error> {
        do { return .success(try await operation()) }
        catch { return .failure(error) }
    }

    private func ownsConfig(_ owner: RequestOwner, requireToken: Bool) -> Bool {
        sessionIdentity() == owner.session && configGeneration == owner.generation
            && !Task.isCancelled && (!requireToken || configUpdatedAt == owner.configToken)
    }

    private func ownsStatus(_ owner: RequestOwner) -> Bool {
        sessionIdentity() == owner.session && statusGeneration == owner.generation
            && configUpdatedAt == owner.configToken && !Task.isCancelled
    }

    private func ownsConflictFetch(
        configOwner: RequestOwner, conflictGeneration generation: UInt64
    ) -> Bool {
        conflictGeneration == generation && ownsConfig(configOwner, requireToken: true)
    }

    private func invalidateConflictFetch() {
        conflictGeneration &+= 1
        conflictingConfig = nil
    }

    private func ownsOperation(
        _ session: String, _ generation: UInt64, current: UInt64
    ) -> Bool {
        sessionIdentity() == session && ownsOperationSlot(generation, current: current)
            && !Task.isCancelled
    }

    /// Cleanup may run after cancellation or session replacement because it
    /// publishes no server data, but never clears a newer request's busy state.
    private func ownsOperationSlot(_ generation: UInt64, current: UInt64) -> Bool {
        current == generation
    }

    private func applyFullConfig(_ config: Components.Schemas.BackupConfig) {
        host = config.smbHost ?? ""
        share = config.smbShare ?? ""
        folder = config.smbFolder ?? ""
        username = config.smbUsername ?? ""
        domain = config.smbDomain ?? ""
        hasStoredPassword = config.hasPassword
        passwordEdited = false
        password = ""
        frequency = Self.mapFrequency(config.frequency)
        localRetention = Self.retentionDraft(
            config.localRetention, maxBytes: config.localMaxBytes,
            reserveBytes: config.localMinFreeBytes)
        offboxRetention = Self.retentionDraft(
            config.offboxRetention, maxBytes: config.offboxMaxBytes,
            reserveBytes: config.offboxMinFreeBytes)
        serverOperational = operationalDraft.withoutPassword()
        serverLocalRetention = localRetention
        serverOffboxRetention = offboxRetention
        configUpdatedAt = config.updatedAt
        latest = config.latest
        updateConfigMetadata(config)
    }

    private func updateConfigMetadata(_ config: Components.Schemas.BackupConfig) {
        retentionReviewRequired = config.retentionReviewRequired
        retentionActivatedAt = config.retentionActivatedAt
        localPendingPruneCount = config.localPendingPruneCount
        localPendingPruneBytes = config.localPendingPruneBytes
        offboxPendingPruneCount = config.offboxPendingPruneCount
        offboxPendingPruneBytes = config.offboxPendingPruneBytes
        legacyConflictDetected = config.legacyConflictDetected
    }

    static func mapFrequency(_ raw: Components.Schemas.BackupConfig.FrequencyPayload?)
        -> Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload
    {
        guard let raw else { return .daily }
        return .init(rawValue: raw.rawValue) ?? .daily
    }

    static func retentionDraft(
        _ policy: Components.Schemas.BackupRetentionPolicy,
        maxBytes: Int64?, reserveBytes: Int64
    ) -> BackupRetentionDraft {
        .init(
            mode: .init(rawValue: policy.mode.rawValue) ?? .tiered,
            keepAllDays: policy.keepAllDays ?? 3,
            dailyUntilDays: policy.dailyUntilDays ?? 14,
            weeklyUntilDays: policy.weeklyUntilDays ?? 90,
            maxGB: maxBytes.map { Double($0) / 1_000_000_000 } ?? 0,
            reserveGB: Double(reserveBytes) / 1_000_000_000)
    }

    static func validationMessage(for draft: BackupRetentionDraft, destination: String) -> String? {
        guard draft.maxGB.isFinite, draft.maxGB >= 0,
            draft.reserveGB.isFinite, draft.reserveGB >= 0
        else {
            return String(localized: "\(destination): sizes cannot be negative.")
        }
        guard draft.mode == .tiered else { return nil }
        guard (1...3650).contains(draft.keepAllDays),
            (1...3650).contains(draft.dailyUntilDays),
            (1...3650).contains(draft.weeklyUntilDays)
        else {
            return String(localized: "\(destination): retention days must be between 1 and 3650.")
        }
        guard draft.keepAllDays <= draft.dailyUntilDays,
            draft.dailyUntilDays <= draft.weeklyUntilDays
        else {
            return String(localized: "\(destination): each retention horizon must be at least the one before it.")
        }
        return nil
    }

    static func retentionSummary(_ draft: BackupRetentionDraft) -> String {
        if draft.mode == .keepAll { return String(localized: "Keep every backup") }
        return String(
            localized: "Every backup for \(draft.keepAllDays) days · one daily through \(draft.dailyUntilDays) days · one weekly through \(draft.weeklyUntilDays) days")
    }

    static func policySummary(_ policy: Components.Schemas.BackupRetentionPolicy) -> String {
        if policy.mode == .keepAll { return String(localized: "Keep every backup") }
        return String(
            localized: "Every backup for \(policy.keepAllDays ?? 0) days · one daily through \(policy.dailyUntilDays ?? 0) days · one weekly through \(policy.weeklyUntilDays ?? 0) days")
    }

    static func destinationStateTitle(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus.StatusPayload
    ) -> String {
        switch status {
        case .notConfigured: return String(localized: "Not configured")
        case .empty: return String(localized: "Empty")
        case .healthy: return String(localized: "Healthy")
        case .constrained: return String(localized: "Constrained")
        case .degraded: return String(localized: "Degraded")
        case .unavailable: return String(localized: "Unavailable")
        }
    }

    static func coverageTitle(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus.CoverageStatusPayload
    ) -> String {
        switch status {
        case .notApplicable: return String(localized: "Not applicable")
        case .empty: return String(localized: "Empty")
        case .building: return String(localized: "Building")
        case .met: return String(localized: "Target met")
        case .incomplete: return String(localized: "Incomplete")
        case .shortened: return String(localized: "Shortened")
        case .unknown: return String(localized: "Unknown")
        }
    }

    static func probeTitle(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus.ProbeStatusPayload
    ) -> String {
        switch status {
        case .complete: return String(localized: "Complete")
        case .partial: return String(localized: "Partial")
        case .unavailable: return String(localized: "Unavailable")
        }
    }

    static func capacityTitle(_ status: Components.Schemas.BackupCapacityObservation.StatusPayload) -> String {
        switch status {
        case .ok: return String(localized: "OK")
        case .warning: return String(localized: "Warning")
        case .insufficient: return String(localized: "Insufficient")
        case .unknown: return String(localized: "Unknown")
        case .unavailable: return String(localized: "Unavailable")
        }
    }

    static func destinationStateMessage(
        _ status: Components.Schemas.BackupDestinationRecoveryStatus
    ) -> String? {
        if status.status == .notConfigured {
            return String(localized: "No off-box destination is configured.")
        }
        if status.status == .unavailable {
            return status.destination == .offbox
                ? String(localized: "Synology inventory is unavailable, so its recovery window is unknown.")
                : String(localized: "On-box inventory is unavailable, so its recovery window is unknown.")
        }
        if status.status == .empty {
            return String(localized: "No readable backups are currently visible.")
        }
        switch status.coverageStatus {
        case .building:
            return String(localized: "Coverage is still building toward the configured target.")
        case .shortened:
            return String(localized: "Storage limits shortened the configured recovery window.")
        case .incomplete:
            return String(localized: "The configured recovery coverage is incomplete.")
        case .unknown:
            return String(localized: "Recovery coverage cannot currently be established.")
        default:
            return nil
        }
    }

    static func capacityMessage(_ capacity: Components.Schemas.BackupCapacityObservation) -> String {
        if capacity.status == .unknown {
            return String(localized: "Capacity could not be measured; backups will still be attempted.")
        }
        if capacity.status == .unavailable {
            return String(localized: "Capacity is unavailable for this destination.")
        }
        var parts: [String] = []
        if let available = capacity.availableBytes {
            let availableText = ByteCountFormatter.string(fromByteCount: available, countStyle: .file)
            if let total = capacity.totalBytes {
                let totalText = ByteCountFormatter.string(fromByteCount: total, countStyle: .file)
                parts.append(String(localized: "\(availableText) available of \(totalText)"))
            } else {
                parts.append(String(localized: "\(availableText) available"))
            }
        }
        if let estimate = capacity.estimatedNextBackupBytes {
            let text = ByteCountFormatter.string(fromByteCount: estimate, countStyle: .file)
            parts.append(String(localized: "next backup estimate \(text)"))
        }
        let reserve = ByteCountFormatter.string(fromByteCount: capacity.reserveBytes, countStyle: .file)
        parts.append(String(localized: "\(reserve) reserved"))
        return parts.joined(separator: " · ")
    }
}
