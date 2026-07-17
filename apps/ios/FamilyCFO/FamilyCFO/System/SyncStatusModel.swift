import Foundation
import Observation

/// One shared source of truth for "when did we last pull from the banks" (M103),
/// so every screen shows the *same* freshness line instead of each computing its
/// own. Persisted so it survives launches and is correct the instant any tab
/// appears. Seeded from the server's value on load and bumped to "now" whenever a
/// sync completes.
@MainActor
@Observable
final class SyncStatusModel {
    private let defaultsKey = "familyCFO.lastSyncedAt"

    private(set) var lastSyncedAt: Date?

    init() {
        lastSyncedAt = UserDefaults.standard.object(forKey: defaultsKey) as? Date
    }

    /// A sync just finished — the data is fresh as of now.
    func markSynced() {
        lastSyncedAt = Date()
        persist()
    }

    /// Adopt the server's reported last-sync time (e.g. from the Overview
    /// context), so background/scheduled syncs are reflected too. Only moves the
    /// clock forward.
    func observe(_ serverDate: Date?) {
        guard let serverDate else { return }
        if let current = lastSyncedAt, serverDate <= current { return }
        lastSyncedAt = serverDate
        persist()
    }

    /// "Last synced 4 minutes ago", or nil when nothing has synced yet.
    var lastSyncedText: String? {
        guard let date = lastSyncedAt else { return nil }
        let elapsed = RelativeDateTimeFormatter()
        elapsed.unitsStyle = .full
        return "Last synced " + elapsed.localizedString(for: date, relativeTo: Date())
    }

    private func persist() {
        UserDefaults.standard.set(lastSyncedAt, forKey: defaultsKey)
    }
}
