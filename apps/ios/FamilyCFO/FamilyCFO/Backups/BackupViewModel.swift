import Foundation

/// Drives the Backup settings screen (M98): Synology SMB credentials + schedule,
/// with auto-save (no Save button), a connection test, back-up-now, and
/// restore-from-share.
@MainActor
@Observable
final class BackupViewModel {
    private let api: BackupAPI

    var host = ""
    var share = ""
    var folder = ""
    var username = ""
    var password = ""
    var domain = ""
    var frequency: Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload = .daily
    /// Max combined backup size in GB; 0 = no cap.
    var maxGB: Double = 0

    private(set) var hasStoredPassword = false
    private(set) var passwordEdited = false
    private(set) var revealedKey: String?
    private(set) var latest: Components.Schemas.BackupJob?
    private(set) var localBackups: [Components.Schemas.BackupJob] = []
    private(set) var remoteBackups: [Components.Schemas.RemoteBackup] = []

    private(set) var isLoading = false
    private(set) var isBackingUp = false
    private(set) var isRestoring = false
    private(set) var isChecking = false
    private(set) var checkResult: Components.Schemas.BackupDestinationCheckResponse?
    var statusMessage: String?
    var errorMessage: String?

    init(api: BackupAPI) { self.api = api }

    private var draft: BackupConfigDraft {
        BackupConfigDraft(
            frequency: frequency, host: host, share: share, folder: folder,
            username: username,
            // Only send the password when the user actually typed one this session.
            password: passwordEdited ? password : nil,
            domain: domain,
            maxBytes: maxGB > 0 ? Int64(maxGB * 1_000_000_000) : nil)
    }

    var latestSummary: String? {
        guard let latest else { return nil }
        if latest.status == .failed {
            return "Last backup failed" + (latest.errorMessage.map { ": \($0)" } ?? ".")
        }
        var parts: [String] = []
        if let when = latest.completedAt {
            parts.append(when.formatted(date: .abbreviated, time: .shortened))
        }
        if let size = latest.sizeBytes {
            parts.append(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
        }
        return parts.isEmpty ? "Completed" : parts.joined(separator: " · ")
    }

    var remoteWarning: String? {
        guard let latest, !host.isEmpty else { return nil }
        if latest.remoteStatus == "failed" {
            return "Last copy to Synology failed" + (latest.remoteError.map { ": \($0)" } ?? ".")
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
        isLoading = true
        defer { isLoading = false }
        do {
            let config = try await api.config()
            host = config.smbHost ?? ""
            share = config.smbShare ?? ""
            folder = config.smbFolder ?? ""
            username = config.smbUsername ?? ""
            domain = config.smbDomain ?? ""
            hasStoredPassword = config.hasPassword ?? false
            passwordEdited = false
            password = ""
            maxGB = config.maxBytes.map { Double($0) / 1_000_000_000 } ?? 0
            frequency = Self.mapFrequency(config.frequency)
            latest = config.latest
            errorMessage = nil
            await loadBackups()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The on-box list is always fetched; the Synology list only when configured.
    func loadBackups() async {
        localBackups = (try? await api.localBackups()) ?? []
        if !host.isEmpty { await loadRemote() } else { remoteBackups = [] }
    }

    func restoreLocal(_ backup: Components.Schemas.BackupJob) async {
        isRestoring = true
        defer { isRestoring = false }
        do {
            try await api.restoreLocal(id: backup.id)
            statusMessage = "Restored. Reopen the app to see restored data."
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func passwordChanged() { passwordEdited = true }

    /// Auto-save — called when any field commits or the schedule changes.
    func save() async {
        do {
            let config = try await api.updateConfig(draft)
            latest = config.latest
            hasStoredPassword = config.hasPassword ?? false
            if passwordEdited { password = ""; passwordEdited = false }  // stored now
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func testConnection() async {
        isChecking = true
        defer { isChecking = false }
        do {
            checkResult = try await api.checkConnection(draft)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func backupNow() async {
        isBackingUp = true
        defer { isBackingUp = false }
        do {
            latest = try await api.backupNow()
            statusMessage = "Backup complete."
            errorMessage = nil
            await loadBackups()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func loadRemote() async {
        remoteBackups = (try? await api.remoteBackups()) ?? []
    }

    func revealKey() async {
        do {
            revealedKey = try await api.encryptionKey()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func deleteLocal(_ backup: Components.Schemas.BackupJob) async {
        do {
            try await api.deleteLocal(id: backup.id)
            await loadBackups()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func deleteRemote(_ backup: Components.Schemas.RemoteBackup) async {
        do {
            try await api.deleteRemote(filename: backup.filename)
            await loadRemote()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func restore(_ backup: Components.Schemas.RemoteBackup) async {
        isRestoring = true
        defer { isRestoring = false }
        do {
            try await api.restoreRemote(filename: backup.filename)
            statusMessage = "Restored from \(backup.filename). Reopen the app to see restored data."
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    static func mapFrequency(_ raw: Components.Schemas.BackupConfig.FrequencyPayload?)
        -> Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload
    {
        guard let raw else { return .daily }
        return .init(rawValue: raw.rawValue) ?? .daily
    }
}
