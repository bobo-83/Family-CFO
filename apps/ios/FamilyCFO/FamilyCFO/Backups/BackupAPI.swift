import Foundation

/// Off-box backups to a Synology over SMB (M98): the app collects the Synology
/// address + credentials and the server uploads encrypted backups directly — no
/// host mount. Configure, test the connection, back up now, and restore from the
/// share.
protocol BackupAPI: Sendable {
    func config() async throws -> Components.Schemas.BackupConfig
    func updateConfig(_ update: BackupConfigDraft) async throws -> Components.Schemas.BackupConfig
    func checkConnection(_ draft: BackupConfigDraft) async throws
        -> Components.Schemas.BackupDestinationCheckResponse
    func backupNow() async throws -> Components.Schemas.BackupJob
    /// Backups stored on the box itself — always available, the everyday restore.
    func localBackups() async throws -> [Components.Schemas.BackupJob]
    func restoreLocal(id: String) async throws
    func remoteBackups() async throws -> [Components.Schemas.RemoteBackup]
    func restoreRemote(filename: String) async throws
    func deleteLocal(id: String) async throws
    func deleteRemote(filename: String) async throws
    /// The key that decrypts every backup — for the owner to store safely.
    func encryptionKey() async throws -> String?
}

/// The editable backup settings. `password` is nil unless the user typed one this
/// session (so leaving it blank keeps the stored password).
struct BackupConfigDraft {
    var frequency: Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload
    var host: String
    var share: String
    var folder: String
    var username: String
    var password: String?
    var domain: String
    var maxBytes: Int64?
}

struct LiveBackupAPI: BackupAPI {
    let client: Client

    func config() async throws -> Components.Schemas.BackupConfig {
        switch try await client.getBackupConfig(.init()) {
        case .ok(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func updateConfig(_ d: BackupConfigDraft) async throws -> Components.Schemas.BackupConfig {
        let body = Components.Schemas.BackupConfigUpdateRequest(
            frequency: d.frequency,
            smbHost: nilIfBlank(d.host),
            smbShare: nilIfBlank(d.share),
            smbFolder: nilIfBlank(d.folder),
            smbUsername: nilIfBlank(d.username),
            smbPassword: d.password,
            smbDomain: nilIfBlank(d.domain),
            maxBytes: d.maxBytes)
        switch try await client.updateBackupConfig(.init(body: .json(body))) {
        case .ok(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func checkConnection(_ d: BackupConfigDraft) async throws
        -> Components.Schemas.BackupDestinationCheckResponse
    {
        let body = Components.Schemas.BackupDestinationCheckRequest(
            smbHost: d.host, smbShare: d.share, smbFolder: nilIfBlank(d.folder),
            smbUsername: d.username, smbPassword: d.password, smbDomain: nilIfBlank(d.domain))
        switch try await client.checkBackupDestination(.init(body: .json(body))) {
        case .ok(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func backupNow() async throws -> Components.Schemas.BackupJob {
        switch try await client.createBackup(.init()) {
        case .created(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func localBackups() async throws -> [Components.Schemas.BackupJob] {
        switch try await client.listBackups(.init()) {
        case .ok(let r):
            return try r.body.json.backups.filter { $0.status == .completed && $0.prunedAt == nil }
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func restoreLocal(id: String) async throws {
        switch try await client.restoreBackup(.init(path: .init(backupId: id))) {
        case .ok: return
        case .badRequest: throw BackupError.restoreFailed
        case .notFound: throw BackupError.notFoundOnShare
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func remoteBackups() async throws -> [Components.Schemas.RemoteBackup] {
        switch try await client.listRemoteBackups(.init()) {
        case .ok(let r): return try r.body.json.backups
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func restoreRemote(filename: String) async throws {
        let body = Components.Schemas.RemoteRestoreRequest(filename: filename)
        switch try await client.restoreRemoteBackup(.init(body: .json(body))) {
        case .ok: return
        case .badRequest: throw BackupError.restoreFailed
        case .notFound: throw BackupError.notFoundOnShare
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func deleteLocal(id: String) async throws {
        switch try await client.deleteBackup(.init(path: .init(backupId: id))) {
        case .noContent, .notFound: return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func deleteRemote(filename: String) async throws {
        let body = Components.Schemas.RemoteRestoreRequest(filename: filename)
        switch try await client.deleteRemoteBackup(.init(body: .json(body))) {
        case .ok: return
        case .badRequest: throw BackupError.restoreFailed
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func encryptionKey() async throws -> String? {
        switch try await client.getBackupEncryptionKey(.init()) {
        case .ok(let r): return try r.body.json.key
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    private func nilIfBlank(_ s: String) -> String? {
        let t = s.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t
    }
}

enum BackupError: Error, LocalizedError {
    case notFoundOnShare
    case restoreFailed

    var errorDescription: String? {
        switch self {
        case .notFoundOnShare: return "That backup is no longer on the share."
        case .restoreFailed:
            return "Couldn't restore from that backup — check the connection and that the file is intact."
        }
    }
}
