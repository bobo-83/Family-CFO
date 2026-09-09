import Foundation
import Testing

@testable import FamilyCFO

struct WatchSharedStateTests {
    private func snapshot(
        safeToSpendMinor: Int64?, netWorthMinor: Int64 = 90_000
    ) -> WatchFaceSnapshot {
        WatchFaceSnapshot(
            leftToSpendMinor: nil,
            safeToSpendMinor: safeToSpendMinor,
            lowestBalanceMinor: nil,
            netWorthMinor: netWorthMinor,
            monthIncomeMinor: nil,
            monthSpendingMinor: nil,
            expectedIncomeMinor: nil,
            currency: "USD",
            capturedAt: Date(),
            budgets: nil)
    }

    @Test func sessionTransitionsAdvanceOnlyWhenTheFinalIdentityChanges() {
        #expect(WatchSessionTransition.changed(from: nil, to: "A"))
        #expect(!WatchSessionTransition.changed(from: "A", to: "A"))
        #expect(WatchSessionTransition.changed(from: "A", to: "B"))
        #expect(WatchSessionTransition.changed(from: "A", to: nil))
        #expect(
            !WatchSessionTransition.shouldClearSnapshot(
                from: nil, to: "A", persist: false))
    }

    @Test func persistedSessionReplacementClearsThePrimitiveSnapshot() {
        let suite = "test.watch.session.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = WatchFaceSnapshotStore(suiteName: suite)
        store.save(snapshot(safeToSpendMinor: 12_345))

        if WatchSessionTransition.shouldClearSnapshot(
            from: "household-a:token-a", to: "household-b:token-b", persist: true)
        {
            store.clear()
        }

        #expect(store.load() == nil)
    }

    @Test func blockerCopyLabelsEachUnavailableCheckWithoutAddingCounts() {
        let subscriptionOnly = WatchSafeToSpendBlockers.copy(
            subscriptionIncompleteCount: 1, savingsIncompleteCount: nil)
        #expect(subscriptionOnly?.contains("Subscription forecast") == true)
        #expect(subscriptionOnly?.contains("1 stored amount") == true)
        #expect(subscriptionOnly?.contains("Savings detection") == false)

        let savingsOnly = WatchSafeToSpendBlockers.copy(
            subscriptionIncompleteCount: nil, savingsIncompleteCount: 4)
        #expect(savingsOnly?.contains("Savings detection") == true)
        #expect(savingsOnly?.contains("4 stored amounts") == true)
        #expect(savingsOnly?.contains("Subscription forecast") == false)

        let both = WatchSafeToSpendBlockers.copy(
            subscriptionIncompleteCount: 2, savingsIncompleteCount: 3)
        #expect(both?.contains("Subscription forecast") == true)
        #expect(both?.contains("Savings detection") == true)
        #expect(both?.contains("per check") == true)
        #expect(both?.contains("may overlap") == true)
        #expect(both?.contains("5 stored amounts") == false)
        #expect(
            WatchSafeToSpendBlockers.copy(
                subscriptionIncompleteCount: nil, savingsIncompleteCount: nil) == nil)
    }

    @Test func contextOnlyCommitClearsOldOptionalHeadlineBeforeEnrichment() {
        let suite = "test.watch.context-only.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = WatchFaceSnapshotStore(suiteName: suite)
        store.save(
            WatchFaceSnapshot(
                leftToSpendMinor: 45_000,
                safeToSpendMinor: 40_000,
                lowestBalanceMinor: 30_000,
                netWorthMinor: 80_000,
                monthIncomeMinor: 500_000,
                monthSpendingMinor: 200_000,
                expectedIncomeMinor: 600_000,
                currency: "USD",
                capturedAt: Date(),
                budgets: [.init(name: "Food", limitMinor: 50_000, spentMinor: 25_000)],
                budgetedMinor: 50_000,
                budgetSpentMinor: 25_000,
                budgetSpentIncompleteCount: 0,
                budgetOverCount: 0,
                budgetWarningCount: 0))
        #expect(store.load()?.headline?.label == "Left to spend")

        // This is the cache state written synchronously after context succeeds.
        // Even if the following optional request hangs or the task is cancelled,
        // no plan, outlook, or budget decision from the prior load remains.
        store.save(
            .contextOnly(
                safeToSpendMinor: nil,
                netWorthMinor: 90_000,
                netWorthIncompleteCount: 2,
                currency: "USD"))

        let committed = store.load()
        #expect(committed?.leftToSpendMinor == nil)
        #expect(committed?.safeToSpendMinor == nil)
        #expect(committed?.lowestBalanceMinor == nil)
        #expect(committed?.monthIncomeMinor == nil)
        #expect(committed?.monthSpendingMinor == nil)
        #expect(committed?.expectedIncomeMinor == nil)
        #expect(committed?.budgets == nil)
        #expect(committed?.budgetedMinor == nil)
        #expect(committed?.budgetSpentMinor == nil)
        #expect(committed?.budgetSpentIncompleteCount == nil)
        #expect(committed?.budgetOverCount == nil)
        #expect(committed?.budgetWarningCount == nil)
        #expect(committed?.headline?.label == "Net worth")
        #expect(committed?.headline?.amountMinor == 90_000)
        #expect(committed?.headline?.incompleteCount == 2)
    }

    @Test func successfulUnavailableSnapshotOverwritesAnExactAmountWithNil() {
        let suite = "test.watch.snapshot.\(UUID().uuidString)"
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        let store = WatchFaceSnapshotStore(suiteName: suite)
        store.save(snapshot(safeToSpendMinor: 12_345))
        #expect(store.load()?.headline?.label == "Safe to spend")

        // Glance writes a complete replacement from each successful context;
        // unavailable decisions are explicit nil rather than a merge with cache.
        store.save(snapshot(safeToSpendMinor: nil, netWorthMinor: 90_000))

        #expect(store.load()?.safeToSpendMinor == nil)
        #expect(store.load()?.headline?.label == "Net worth")
        #expect(store.load()?.headline?.amountMinor == 90_000)
    }
}
