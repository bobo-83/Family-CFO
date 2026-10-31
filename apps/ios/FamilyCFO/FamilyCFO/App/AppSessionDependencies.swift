import Foundation

/// Persistence used by the session lifecycle. Production uses platform stores;
/// tests use an isolated preferences suite and in-memory credential storage.
struct AppSessionStorage: Sendable {
    var readServer: @Sendable () -> Data?
    var writeServer: @Sendable (Data?) -> Void
    var readLanguage: @Sendable () -> String?
    var writeLanguage: @Sendable (String) -> Void
    var readCredential: @Sendable (String) -> Data?
    var writeCredential: @Sendable (Data, String) throws -> Void
    var deleteCredential: @Sendable (String) -> Void
    var makeSyncStatus: @MainActor @Sendable (String?) -> SyncStatusModel

    static let live = Self(
        readServer: { UserDefaults.standard.data(forKey: AppModel.serverDefaultsKey) },
        writeServer: { UserDefaults.standard.set($0, forKey: AppModel.serverDefaultsKey) },
        readLanguage: { UserDefaults.standard.string(forKey: AppModel.languageKey) },
        writeLanguage: { UserDefaults.standard.set($0, forKey: AppModel.languageKey) },
        readCredential: { KeychainStore.load(account: $0) },
        writeCredential: { try KeychainStore.save($0, account: $1) },
        deleteCredential: { KeychainStore.delete(account: $0) },
        makeSyncStatus: { SyncStatusModel(scope: $0) }
    )
}

/// External session effects stay live by default. Isolated tests replace these
/// callbacks, rather than clearing host widgets/notifications or contacting a box.
struct AppSessionEffects: Sendable {
    var registerShortcuts: @MainActor @Sendable () -> Void
    var clearArtifacts: @MainActor @Sendable (String) -> Void
    var activateWatch: @MainActor @Sendable () -> Void
    var pushWatch: @MainActor @Sendable (ServerConfig?, StoredCredential?) -> Void
    var revokeSession: @MainActor @Sendable (Client) async -> Void

    static let live = Self(
        registerShortcuts: { FamilyCFOShortcuts.updateAppShortcutParameters() },
        clearArtifacts: { currentScope in
            Task {
                await BillNotificationScheduler(scheduler: SystemNotificationScheduler())
                    .clear(exceptScope: currentScope)
            }
            OverviewSnapshotStore().clear()
            WidgetRefresher.reloadOverview()
        },
        activateWatch: { PhoneWatchBridge.shared.activate() },
        pushWatch: { PhoneWatchBridge.shared.push(server: $0, credential: $1) },
        revokeSession: { _ = try? await $0.deleteAuthSession(.init()) }
    )
}
