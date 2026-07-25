import Foundation

/// The last-known glance values the home-screen widget shows (M92a).
///
/// The widget must not poll the box (battery, and the box may be off-network),
/// so the app writes this snapshot to a shared App Group container every time it
/// loads the Overview, and the widget reads whatever was last written. Stale is
/// fine and honest — the snapshot carries its own timestamp so the widget can
/// say "as of ...".
struct OverviewSnapshot: Codable, Equatable {
    var netWorthMinor: Int64
    var currency: String
    var emergencyFundStatus: String
    var emergencyFundMonths: Double?
    var capturedAt: Date

    /// The App Group both the app and the widget use. Must match the
    /// `com.apple.security.application-groups` entitlement on both targets.
    static let appGroup = "group.com.familycfo.ios"
    static let key = "overview-snapshot"
    /// The widget kind, shared so the app can reload exactly this widget and the
    /// widget can register under the same name. Lives here (a file with no API
    /// dependency) so it can be compiled into the widget target cleanly.
    static let widgetKind = "FamilyCFOOverviewWidget"
}

/// Reads/writes the snapshot through the shared container. Falls back to standard
/// defaults when the App Group isn't available (unit tests, or a build without
/// the entitlement), so the store is always usable and never crashes.
struct OverviewSnapshotStore {
    private let defaults: UserDefaults

    init(suiteName: String? = OverviewSnapshot.appGroup) {
        self.defaults = suiteName.flatMap { UserDefaults(suiteName: $0) } ?? .standard
    }

    func save(_ snapshot: OverviewSnapshot) {
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        defaults.set(data, forKey: OverviewSnapshot.key)
    }

    func load() -> OverviewSnapshot? {
        guard let data = defaults.data(forKey: OverviewSnapshot.key) else { return nil }
        return try? JSONDecoder().decode(OverviewSnapshot.self, from: data)
    }

    func clear() {
        defaults.removeObject(forKey: OverviewSnapshot.key)
    }
}

extension OverviewSnapshot {
    var netWorthFormatted: String {
        (Decimal(netWorthMinor) / 100)
            .formatted(.currency(code: currency).precision(.fractionLength(0)))
    }
}
