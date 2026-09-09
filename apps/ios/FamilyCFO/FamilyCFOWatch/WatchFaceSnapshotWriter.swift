import Foundation
import WidgetKit

/// Watch-app-side snapshot plumbing (the shared struct can't see the
/// generated API types — the widget target has no client).
extension WatchFaceSnapshot {
    static func slices(from budgets: [Components.Schemas.Budget]) -> [BudgetSlice] {
        budgets.map {
            BudgetSlice(
                name: $0.categoryName,
                limitMinor: $0.limit.amountMinor,
                spentMinor: $0.spent.amountMinor,
                spentIncompleteCount: $0.spent.incompleteCount,
                percentUsed: $0.percentUsed)
        }
        .sorted {
            switch ($0.percentUsed, $1.percentUsed) {
            case let (lhs?, rhs?): lhs > rhs
            case (_?, nil): true
            case (nil, _?): false
            case (nil, nil): $0.name < $1.name
            }
        }
    }

    /// Refresh the cache from the full server response. Summary values are
    /// copied directly; the widget must not recreate aggregate arithmetic from
    /// category slices. A fresh nil decision overwrites any stale exact value.
    static func refreshBudgetCache(_ response: Components.Schemas.BudgetListResponse) {
        let store = WatchFaceSnapshotStore()
        var snapshot = store.load() ?? WatchFaceSnapshot(
            leftToSpendMinor: nil,
            safeToSpendMinor: nil,
            lowestBalanceMinor: nil,
            netWorthMinor: nil,
            monthIncomeMinor: nil,
            monthSpendingMinor: nil,
            expectedIncomeMinor: nil,
            currency: response.summary.totalBudgeted.currency,
            capturedAt: Date(),
            budgets: nil)
        snapshot.budgets = slices(from: response.budgets)
        snapshot.budgetedMinor = response.summary.totalBudgeted.amountMinor
        snapshot.budgetSpentMinor = response.summary.totalSpent.amountMinor
        snapshot.budgetSpentIncompleteCount = response.summary.totalSpent.incompleteCount
        snapshot.budgetOverCount = response.summary.overCount
        snapshot.budgetWarningCount = response.summary.warningCount
        snapshot.capturedAt = Date()
        store.save(snapshot)
        WidgetCenter.shared.reloadAllTimelines()
    }
}
