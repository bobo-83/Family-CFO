import Foundation

@testable import FamilyCFO

/// Thread-safe in-memory replacement for the platform credential store.
final class TestSessionCredentials: @unchecked Sendable {
    enum Failure: Error { case syntheticWriteFailure }
    private let lock = NSLock()
    private var values: [String: Data] = [:]
    private var failedAccounts: Set<String> = []
    private var attempts: [String] = []

    func read(_ account: String) -> Data? { lock.withLock { values[account] } }
    func delete(_ account: String) { lock.withLock { values[account] = nil } }
    func failWrites(to account: String) { lock.withLock { _ = failedAccounts.insert(account) } }
    var writeAttempts: [String] { lock.withLock { attempts } }

    func write(_ data: Data, account: String) throws {
        try lock.withLock {
            attempts.append(account)
            if failedAccounts.contains(account) { throw Failure.syntheticWriteFailure }
            values[account] = data
        }
    }
}

/// No standard preferences, real Keychain, widgets, notifications, shortcuts,
/// watch bridge or network revocation may be touched by a synthetic restoration.
@MainActor
final class SessionTestHarness {
    let suite = "SessionTestHarness.\(UUID().uuidString)"
    let credentials = TestSessionCredentials()
    private(set) var artifactClears = 0
    private(set) var watchActivations = 0
    private(set) var watchDeviceIDs: [String?] = []
    private(set) var revocations = 0

    var defaults: UserDefaults { UserDefaults(suiteName: suite)! }

    var storage: AppSessionStorage {
        let suite = suite
        let credentials = credentials
        return AppSessionStorage(
            readServer: { UserDefaults(suiteName: suite)!.data(forKey: AppModel.serverDefaultsKey) },
            writeServer: { UserDefaults(suiteName: suite)!.set($0, forKey: AppModel.serverDefaultsKey) },
            readLanguage: { UserDefaults(suiteName: suite)!.string(forKey: AppModel.languageKey) },
            writeLanguage: { UserDefaults(suiteName: suite)!.set($0, forKey: AppModel.languageKey) },
            readCredential: { credentials.read($0) },
            writeCredential: { try credentials.write($0, account: $1) },
            deleteCredential: { credentials.delete($0) },
            makeSyncStatus: { SyncStatusModel(scope: $0, defaults: UserDefaults(suiteName: suite)!) }
        )
    }

    var effects: AppSessionEffects {
        AppSessionEffects(
            registerShortcuts: {},
            clearArtifacts: { [weak self] _ in self?.artifactClears += 1 },
            activateWatch: { [weak self] in self?.watchActivations += 1 },
            pushWatch: { [weak self] _, credential in self?.watchDeviceIDs.append(credential?.deviceID) },
            revokeSession: { [weak self] _ in self?.revocations += 1 }
        )
    }

    func makeModel() -> AppModel { AppModel(storage: storage, effects: effects) }
    func cleanup() { defaults.removePersistentDomain(forName: suite) }
}
