import Foundation
import WidgetKit

/// Watch-app-side snapshot plumbing (the shared struct can't see the
/// generated API types — the widget target has no client).
extension WatchFaceSnapshot {
    static func slices(from budgets: [Components.Schemas.Budget]) -> [BudgetSlice] {
        budgets.map {
            BudgetSlice(
                name: $0.categoryName,
                limitMinor: Int64($0.limit.amountMinor),
                spentMinor: Int64($0.spent.amountMinor))
        }
        .sorted { $0.fraction > $1.fraction }
    }

    /// The Budgets PAGE also refreshes the complications' budget slices
    /// (user report 2026-07-25: the page said 196% while the face ring still
    /// showed a 101% cached before new transactions synced — only the Glance
    /// page used to write the cache). Read-modify-write so the glance
    /// numbers a Glance load cached stay untouched.
    static func refreshBudgetCache(_ budgets: [Components.Schemas.Budget]) {
        let store = WatchFaceSnapshotStore()
        guard var snapshot = store.load() else { return }
        snapshot.budgets = slices(from: budgets)
        snapshot.capturedAt = Date()
        store.save(snapshot)
        WidgetCenter.shared.reloadAllTimelines()
    }
}
