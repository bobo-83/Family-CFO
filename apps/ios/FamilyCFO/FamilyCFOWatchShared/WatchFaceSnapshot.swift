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
    // Optional completeness metadata keeps old primitive snapshots decodable.
    var netWorthIncompleteCount: Int? = nil
    var monthIncomeIncompleteCount: Int? = nil
    var monthSpendingIncompleteCount: Int? = nil
    var currency: String
    var capturedAt: Date

    /// The budget complications' slices (ADR 0067 v9), pre-sorted by usage
    /// (most at-risk first). Optional: an older cache still decodes.
    var budgets: [BudgetSlice]?
    // Server-owned aggregate primitives. Do not rebuild them from `budgets`.
    var budgetedMinor: Int64? = nil
    var budgetSpentMinor: Int64? = nil
    var budgetSpentIncompleteCount: Int? = nil
    var budgetOverCount: Int? = nil
    var budgetWarningCount: Int? = nil

    struct BudgetSlice: Codable, Equatable {
        var name: String
        var limitMinor: Int64
        var spentMinor: Int64
        var spentIncompleteCount: Int? = nil
        var percentUsed: Int? = nil

        /// Server-owned percentage. A missing decision must not become a zero ring.
        var fraction: Double? {
            percentUsed.map { Double($0) / 100 }
        }
    }

    var budgetStatusLabel: String? {
        guard budgetSpentMinor != nil else { return nil }
        guard let over = budgetOverCount, let warning = budgetWarningCount else {
            return String(localized: "Unavailable")
        }
        if over > 0 { return String(localized: "\(over) over budget") }
        if warning > 0 { return String(localized: "\(warning) near limit") }
        return String(localized: "On track")
    }

    /// Must match the `com.apple.security.application-groups` entitlement on
    /// BOTH the watch app and the watch widget extension.
    static let appGroup = "group.com.familycfo.ios"
    static let key = "watch-face-snapshot"
    static let widgetKind = "FamilyCFOWatchGlance"
    static let budgetsWidgetKind = "FamilyCFOWatchBudgets"
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

    func clear() {
        defaults.removeObject(forKey: WatchFaceSnapshot.key)
    }
}

/// Pure session-boundary predicate shared with the phone test target. Pairing
/// fields may be assigned one at a time, but Watch work advances only once the
/// final identity differs from the prior identity.
enum WatchSessionTransition {
    static func changed(from previous: String?, to next: String?) -> Bool {
        previous != next
    }

    static func shouldClearSnapshot(
        from previous: String?, to next: String?, persist: Bool
    ) -> Bool {
        persist && changed(from: previous, to: next)
    }
}

/// Truthful Watch copy for the two independent safe-to-spend detectors. Counts
/// are deliberately kept per check because their unreadable source sets may
/// overlap and cannot be added into a deduplicated total on the client.
enum WatchSafeToSpendBlockers {
    static func copy(
        subscriptionIncompleteCount: Int?, savingsIncompleteCount: Int?
    ) -> String? {
        var blockers: [String] = []
        if let count = subscriptionIncompleteCount {
            blockers.append(
                count == 1
                    ? String(localized: "Subscription forecast unavailable because 1 stored amount could not be read.")
                    : String(localized: "Subscription forecast unavailable because \(count) stored amounts could not be read."))
        }
        if let count = savingsIncompleteCount {
            blockers.append(
                count == 1
                    ? String(localized: "Savings detection unavailable because 1 stored amount could not be read.")
                    : String(localized: "Savings detection unavailable because \(count) stored amounts could not be read."))
        }
        if blockers.count > 1 {
            blockers.append(String(localized: "Counts are per check and may overlap."))
        }
        return blockers.isEmpty ? nil : blockers.joined(separator: " ")
    }
}

extension WatchFaceSnapshot {
    /// A successful household-context response must replace any older optional
    /// plan, outlook, or budget decisions before those slower requests finish.
    /// This complete primitive snapshot is therefore safe to persist even when
    /// optional enrichment is cancelled or never returns.
    static func contextOnly(
        safeToSpendMinor: Int64?,
        netWorthMinor: Int64,
        netWorthIncompleteCount: Int?,
        currency: String,
        capturedAt: Date = Date()
    ) -> WatchFaceSnapshot {
        WatchFaceSnapshot(
            leftToSpendMinor: nil,
            safeToSpendMinor: safeToSpendMinor,
            lowestBalanceMinor: nil,
            netWorthMinor: netWorthMinor,
            monthIncomeMinor: nil,
            monthSpendingMinor: nil,
            expectedIncomeMinor: nil,
            netWorthIncompleteCount: netWorthIncompleteCount,
            monthIncomeIncompleteCount: nil,
            monthSpendingIncompleteCount: nil,
            currency: currency,
            capturedAt: capturedAt,
            budgets: nil,
            budgetedMinor: nil,
            budgetSpentMinor: nil,
            budgetSpentIncompleteCount: nil,
            budgetOverCount: nil,
            budgetWarningCount: nil)
    }

    /// The one number a face slot leads with: left to spend, else safe to
    /// spend, else net worth — the same priority as the Glance page's rows.
    var headline: (label: String, amountMinor: Int64, incompleteCount: Int)? {
        if let left = leftToSpendMinor {
            return (String(localized: "Left to spend"), left, 0)
        }
        if let safe = safeToSpendMinor {
            return (String(localized: "Safe to spend"), safe, 0)
        }
        if let netWorth = netWorthMinor {
            return (String(localized: "Net worth"), netWorth, netWorthIncompleteCount ?? 0)
        }
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

    /// The small-slot cash meter (user request 2026-07-25): 1 bill = barely
    /// covering the month's expenses, 5 = way more than needed, torn = in the
    /// red. Left-to-spend already nets out bills/obligations, so the level is
    /// the margin as a share of the month's expected income.
    enum CashSignal: Equatable {
        case torn
        case bills(Int)  // 1...5
    }

    var cashSignal: CashSignal? {
        guard let left = leftToSpendMinor else { return nil }
        if left < 0 { return .torn }
        guard let expected = expectedIncomeMinor, expected > 0 else { return nil }
        let fraction = Double(left) / Double(expected)
        switch fraction {
        case ..<0.05: return .bills(1)
        case ..<0.15: return .bills(2)
        case ..<0.25: return .bills(3)
        case ..<0.40: return .bills(4)
        default: return .bills(5)
        }
    }

    func compact(_ minor: Int64) -> String {
        (Decimal(minor) / 100)
            .formatted(
                .currency(code: currency).notation(.compactName)
                    .precision(.fractionLength(0...1)))
    }
}
