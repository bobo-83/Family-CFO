import Foundation

/// The last-known glance values the watch-face complication shows (ADR 0067
/// v5). Same contract as the phone's `OverviewSnapshot` (M92a): the widget
/// never polls the box — the watch app writes this to the shared App Group
/// container every time the Glance page loads, and the complication reads
/// whatever was last written, honestly stamped with its age.
struct WatchFaceSnapshot: Codable, Equatable {
    var leftToSpendMinor: Int64?
    var safeToSpendMinor: Int64?
    var lowestBalanceMinor: Int64?
    var netWorthMinor: Int64?
    // The month picture behind the graphical slots (ADR 0067 v7): optional so
    // a cache written by an older app still decodes (text-only fallback).
    var monthIncomeMinor: Int64?
    var monthSpendingMinor: Int64?
    var expectedIncomeMinor: Int64?
    var currency: String
    var capturedAt: Date

    /// Must match the `com.apple.security.application-groups` entitlement on
    /// BOTH the watch app and the watch widget extension.
    static let appGroup = "group.com.familycfo.ios"
    static let key = "watch-face-snapshot"
    static let widgetKind = "FamilyCFOWatchGlance"
}

/// Reads/writes the snapshot through the shared container. Falls back to
/// standard defaults when the App Group isn't available (a build without the
/// entitlement), so the store is always usable and never crashes.
struct WatchFaceSnapshotStore {
    private let defaults: UserDefaults

    init(suiteName: String? = WatchFaceSnapshot.appGroup) {
        self.defaults = suiteName.flatMap { UserDefaults(suiteName: $0) } ?? .standard
    }

    func save(_ snapshot: WatchFaceSnapshot) {
        guard let data = try? JSONEncoder().encode(snapshot) else { return }
        defaults.set(data, forKey: WatchFaceSnapshot.key)
    }

    func load() -> WatchFaceSnapshot? {
        guard let data = defaults.data(forKey: WatchFaceSnapshot.key) else { return nil }
        return try? JSONDecoder().decode(WatchFaceSnapshot.self, from: data)
    }
}

extension WatchFaceSnapshot {
    /// The one number a face slot leads with: left to spend, else safe to
    /// spend, else net worth — the same priority as the Glance page's rows.
    var headline: (label: String, amountMinor: Int64)? {
        if let left = leftToSpendMinor { return ("Left to spend", left) }
        if let safe = safeToSpendMinor { return ("Safe to spend", safe) }
        if let netWorth = netWorthMinor { return ("Net worth", netWorth) }
        return nil
    }

    func formatted(_ minor: Int64) -> String {
        (Decimal(minor) / 100)
            .formatted(.currency(code: currency).precision(.fractionLength(0)))
    }

    /// Tight face slots ("$12.7K"): compact notation, one decimal at most.
    /// The ring: how much of the month's expected income is still free to
    /// spend, clamped to the gauge's 0...1. nil when the plan is unknown.
    var spendableFraction: Double? {
        guard let left = leftToSpendMinor, let expected = expectedIncomeMinor, expected > 0
        else { return nil }
        return min(max(Double(left) / Double(expected), 0), 1)
    }

    func compact(_ minor: Int64) -> String {
        (Decimal(minor) / 100)
            .formatted(
                .currency(code: currency).notation(.compactName)
                    .precision(.fractionLength(0...1)))
    }
}
