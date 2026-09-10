import Foundation

/// Off-box backups to a Synology over SMB (M98): the app collects the Synology
/// address + credentials and the server uploads encrypted backups directly — no
/// host mount. Configure, test the connection, back up now, and restore from the
/// share.
protocol BackupAPI: Sendable {
    func config() async throws -> Components.Schemas.BackupConfig
    func updateConfig(_ update: BackupConfigDraft) async throws -> Components.Schemas.BackupConfig
    func recoveryStatus() async throws -> Components.Schemas.BackupRecoveryStatus
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
    /// ADR 0072 Phase 2: which unwrap paths exist for the household's data key.
    func householdKeyStatus() async throws -> Components.Schemas.HouseholdKeyStatus
    /// Mints (or replaces) the recovery key — the response is shown ONCE and
    /// can never be retrieved again.
    func generateRecoveryKey() async throws -> Components.Schemas.RecoveryKey
    /// ADR 0072 Phase 3: switch the household between convenient and sealed
    /// privacy modes. The server enforces the preconditions (≥1 member key +
    /// recovery key to seal; unlocked to unseal) and answers 409 with a human
    /// message when they fail.
    func setSealMode(_ mode: Components.Schemas.SealModeRequest.ModePayload) async throws
        -> Components.Schemas.HouseholdKeyStatus
    /// Unlocks a locked household with its recovery key (sealed after a
    /// restart, or convenient restored without its master key). The server
    /// answers 400 with a human message when the key doesn't match.
    func unlockWithRecoveryKey(_ key: String) async throws
        -> Components.Schemas.HouseholdKeyStatus
    /// The box's running version (from /health) — flags backups made by a
    /// NEWER app, which the server refuses to restore (409). Best-effort: nil
    /// must never break the screen.
    func serverVersion() async -> String?
    /// #189: everything in the household — accounts, transactions, advisor
    /// history, documents — as a portable zip. A sealed household that is
    /// locked answers 423 with a human message, shown verbatim.
    func exportData() async throws -> Data
}

/// The editable backup settings. `password` is nil unless the user typed one this
/// session (so leaving it blank keeps the stored password).
struct BackupRetentionDraft: Equatable, Sendable {
    var mode: Components.Schemas.BackupRetentionPolicyUpdate.ModePayload = .tiered
    var keepAllDays = 3
    var dailyUntilDays = 14
    var weeklyUntilDays = 90
    /// Decimal GB, matching the existing settings UI. Zero means no logical cap.
    var maxGB: Double = 0
    /// Decimal GB that must remain caller-available before a new archive is attempted.
    var reserveGB: Double = 1

    var request: Components.Schemas.BackupRetentionPolicyUpdate {
        .init(
            mode: mode,
            keepAllDays: mode == .tiered ? keepAllDays : nil,
            dailyUntilDays: mode == .tiered ? dailyUntilDays : nil,
            weeklyUntilDays: mode == .tiered ? weeklyUntilDays : nil)
    }
}

/// Editable backup settings. Retention is sent only by the explicit activation
/// action; ordinary destination/cadence autosaves deliberately omit it.
struct BackupConfigDraft: Sendable {
    var frequency: Components.Schemas.BackupConfigUpdateRequest.FrequencyPayload
    var host: String
    var share: String
    var folder: String
    var username: String
    var password: String?
    var domain: String
    var localRetention: BackupRetentionDraft
    var offboxRetention: BackupRetentionDraft
    var expectedUpdatedAt: Date?
    var confirmRetentionPolicy: Bool
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
        let includesRetention = d.confirmRetentionPolicy
        let body = Components.Schemas.BackupConfigUpdateRequest(
            frequency: d.frequency,
            smbHost: nilIfBlank(d.host),
            smbShare: nilIfBlank(d.share),
            smbFolder: nilIfBlank(d.folder),
            smbUsername: nilIfBlank(d.username),
            smbPassword: d.password,
            smbDomain: nilIfBlank(d.domain),
            localRetention: includesRetention ? d.localRetention.request : nil,
            offboxRetention: includesRetention ? d.offboxRetention.request : nil,
            localMaxBytes: includesRetention ? bytes(d.localRetention.maxGB, zeroIsNil: true) : nil,
            offboxMaxBytes: includesRetention ? bytes(d.offboxRetention.maxGB, zeroIsNil: true) : nil,
            localMinFreeBytes: includesRetention ? bytes(d.localRetention.reserveGB) : nil,
            offboxMinFreeBytes: includesRetention ? bytes(d.offboxRetention.reserveGB) : nil,
            expectedUpdatedAt: d.expectedUpdatedAt,
            confirmRetentionPolicy: d.confirmRetentionPolicy)
        switch try await client.updateBackupConfig(.init(body: .json(body))) {
        case .ok(let r): return try r.body.json
        case .conflict(let response):
            let message = (try? response.body.json.error.message)
                ?? String(localized: "Backup settings changed on the box.")
            throw BackupError.configurationConflict(message)
        case .unprocessableContent(let response):
            if let message = try? response.body.json.error.message { throw APIError.advisor(message) }
            throw APIError.server(422)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func recoveryStatus() async throws -> Components.Schemas.BackupRecoveryStatus {
        switch try await client.getBackupRecoveryStatus(.init()) {
        case .ok(let response): return try response.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
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
        case .conflict(let response):
            if let message = try? response.body.json.error.message { throw APIError.advisor(message) }
            throw APIError.server(409)
        case .tooManyRequests(let response):
            // The cooldown (#181): the server says how long to wait and that
            // the data is already saved — show that, not a bare status code.
            let message = (try? response.body.json.error.message)
                ?? String(localized: "A backup just ran — try again in a moment.")
            throw APIError.advisor(message)
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
        case .conflict(let response):
            // Backup from a newer app version — the server's message says what
            // to do ("update the app first"); show it verbatim.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(409)
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
        case .conflict(let response):
            // Backup from a newer app version — the server's message says what
            // to do ("update the app first"); show it verbatim.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(409)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func deleteLocal(id: String) async throws {
        switch try await client.deleteBackup(.init(path: .init(backupId: id))) {
        case .noContent, .notFound: return
        case .conflict(let response):
            if let message = try? response.body.json.error.message { throw APIError.advisor(message) }
            throw APIError.server(409)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func deleteRemote(filename: String) async throws {
        let body = Components.Schemas.RemoteRestoreRequest(filename: filename)
        switch try await client.deleteRemoteBackup(.init(body: .json(body))) {
        case .ok(let response):
            let result = try response.body.json
            guard result.writable else {
                throw APIError.advisor(
                    result.reason ?? String(localized: "Backup couldn't reach your Synology."))
            }
            return
        case .badRequest: throw BackupError.restoreFailed
        case .conflict(let response):
            if let message = try? response.body.json.error.message { throw APIError.advisor(message) }
            throw APIError.server(409)
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

    func householdKeyStatus() async throws -> Components.Schemas.HouseholdKeyStatus {
        switch try await client.getHouseholdKeyStatus(.init()) {
        case .ok(let r): return try r.body.json
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func generateRecoveryKey() async throws -> Components.Schemas.RecoveryKey {
        switch try await client.generateRecoveryKey(.init()) {
        case .ok(let r): return try r.body.json
        case .conflict(let response):
            // Encryption off on this box — the server's message says so; show
            // it verbatim.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(409)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func setSealMode(_ mode: Components.Schemas.SealModeRequest.ModePayload) async throws
        -> Components.Schemas.HouseholdKeyStatus
    {
        switch try await client.setSealMode(.init(body: .json(.init(mode: mode)))) {
        case .ok(let r): return try r.body.json
        case .conflict(let response):
            // Preconditions failed (needs a member key + recovery key, or must
            // be unlocked to unseal) — the server's message says so; show it
            // verbatim.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(409)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func unlockWithRecoveryKey(_ key: String) async throws
        -> Components.Schemas.HouseholdKeyStatus
    {
        switch try await client.unlockWithRecoveryKey(.init(body: .json(.init(recoveryKey: key)))) {
        case .ok(let r): return try r.body.json
        case .badRequest(let response):
            // The key doesn't match — the server's message says so; show it
            // verbatim.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(400)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    func serverVersion() async -> String? {
        // Best-effort, like HouseholdAPI.serverVersion — never break the screen.
        guard case .ok(let response) = try? await client.getHealth(.init()),
            let health = try? response.body.json
        else { return nil }
        return health.version
    }

    func exportData() async throws -> Data {
        switch try await client.exportHousehold(.init()) {
        case .ok(let r):
            // Whole-household zips stay modest (CSV + JSON + scanned files),
            // but leave generous headroom for document-heavy families.
            return try await Data(collecting: try r.body.applicationZip, upTo: 1024 * 1024 * 1024)
        case .code423(let response):
            // Sealed household, locked — the server's message says what to do.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(423)
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .conflict: throw APIError.incompleteData
        case .undocumented(let s, _): throw APIError.server(s)
        }
    }

    private func nilIfBlank(_ s: String) -> String? {
        let t = s.trimmingCharacters(in: .whitespaces)
        return t.isEmpty ? nil : t
    }

    private func bytes(_ decimalGB: Double, zeroIsNil: Bool = false) -> Int64? {
        if zeroIsNil && decimalGB == 0 { return nil }
        guard decimalGB.isFinite, decimalGB >= 0,
            decimalGB <= Double(Int64.max) / 1_000_000_000
        else { return nil }
        return Int64((decimalGB * 1_000_000_000).rounded())
    }
}

enum BackupError: Error, LocalizedError {
    case notFoundOnShare
    case restoreFailed
    case configurationConflict(String)

    var errorDescription: String? {
        switch self {
        case .notFoundOnShare: return String(localized: "That backup is no longer on the share.")
        case .configurationConflict(let message): return message
        case .restoreFailed:
            return String(
                localized:
                    "Couldn't restore from that backup — check the connection and that the file is intact."
            )
        }
    }
}
