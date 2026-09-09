import SwiftUI
import WidgetKit

/// The Overview, wrist-sized: safe-to-spend leads (the number the family
/// actually acts on), then net worth and this month's flow — all straight
/// from the same `GET /household` context every other client renders.
struct WatchGlanceView: View {
    @Environment(WatchModel.self) private var model
    @State private var context: Components.Schemas.HouseholdContext?
    @State private var plan: Components.Schemas.SpendingPlanResponse?
    @State private var outlook: Components.Schemas.CashOutlookResponse?
    @State private var budgets: Components.Schemas.BudgetListResponse?
    @State private var errorMessage: String?
    @State private var isLoading = false
    @State private var generation: UInt64 = 0
    @State private var currentOwner: LoadOwner?
    @State private var committedOwner: LoadOwner?

    private struct LoadOwner: Equatable {
        let identity: String
        let revision: UInt64
        let generation: UInt64
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let context, belongsToCurrentSession(committedOwner) {
                    // EXACTLY the phone Overview's headline figures, in its
                    // order — same fields, same server numbers (user report
                    // 2026-07-25: the wrist showed different numbers because
                    // it read the recurring cash-flow MODEL, not the plan).
                    if let outlook {
                        if let low = outlook.lowestBalance {
                            // A runway action is meaningful only with the server decision.
                            if !low.isPartial, let sellBy = outlook.sellByDate {
                                glanceRow(
                                    outlook.runwayAction == .moveCash
                                        ? "Free up cash by" : "Sell RSUs by",
                                    WatchGlanceView.shortDate(sellBy), tint: .red)
                            }
                            glanceRow(
                                "30-day low", low.formatted,
                                tint: low.isPartial ? .primary : (low.amountMinor >= 0 ? .green : .red))
                            if let note = low.partialDisclosure {
                                Text(note).font(.caption2).foregroundStyle(.secondary)
                            }
                        } else {
                            unavailableRow("30-day low", availability: outlook.incomeProjection)
                        }
                    }
                    if let plan {
                        if let left = plan.leftToSpend {
                            glanceRow(
                                "Left to spend", left.formatted,
                                tint: left.isPartial ? .primary : (left.amountMinor >= 0 ? .green : .red))
                            if let note = left.partialDisclosure {
                                Text(note).font(.caption2).foregroundStyle(.secondary)
                            }
                        } else {
                            unavailableRow("Left to spend", availability: plan.incomeProjection)
                        }
                    }
                    if let sts = context.safeToSpend {
                        if let safe = sts.safeToSpend {
                            NavigationLink {
                                WatchSafeToSpendDetail(safeToSpend: sts)
                            } label: {
                                glanceRow(
                                    "Safe to spend", safe.formatted,
                                    tint: safe.isPartial ? .primary : (safe.amountMinor >= 0 ? .green : .red))
                                if let note = safe.partialDisclosure {
                                    Text(note).font(.caption2).foregroundStyle(.secondary)
                                }
                            }
                            .buttonStyle(.plain)
                        } else {
                            unavailableRow(
                                "Safe to spend",
                                note: WatchSafeToSpendBlockers.copy(
                                    subscriptionIncompleteCount:
                                        sts.subscriptionDetection.isUnavailable
                                        ? sts.subscriptionDetection.incompleteCount : nil,
                                    savingsIncompleteCount:
                                        sts.savingsDetection.isUnavailable
                                        ? sts.savingsDetection.incompleteCount : nil))
                        }
                    }
                    NavigationLink {
                        WatchNetWorthDetail(context: context)
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            glanceRow("Net worth", context.netWorth.formatted, tint: .primary)
                            if let note = context.netWorth.partialDisclosure {
                                Text(note).font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                    if let fund = context.emergencyFund, let months = fund.months {
                        glanceRow(
                            "Emergency fund",
                            months.formatted(.number.precision(.fractionLength(1))) + " mo",
                            tint: months < 3 ? .orange : .primary)
                    }
                } else if isLoading, belongsToCurrentSession(currentOwner) {
                    ProgressView()
                } else if let errorMessage, belongsToCurrentSession(currentOwner) {
                    Text(errorMessage).font(.footnote).foregroundStyle(.red)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // Clear of the page-indicator dots (user report 2026-07-25).
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle(model.householdName ?? "Overview")
        .task(id: model.sessionRevision) {
            await load(revision: model.sessionRevision, resetForSession: true)
        }
        .refreshable { await load(revision: model.sessionRevision) }
    }

    /// "2026-08-05" -> "Tue, Aug 5" (mirrors BillsView.shortDate, which lives
    /// in the phone target).
    static func shortDate(_ iso: String) -> String {
        let parser = DateFormatter()
        parser.calendar = Calendar(identifier: .gregorian)
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: String(iso.prefix(10))) else { return iso }
        return date.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())
    }

    private func glanceRow(_ label: String, _ value: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).foregroundStyle(tint)
        }
        .accessibilityElement(children: .combine)
    }

    private func unavailableRow(
        _ label: String,
        availability: Components.Schemas.ComputationAvailability
    ) -> some View {
        unavailableRow(label, note: availability.unavailableDisclosure)
    }

    private func unavailableRow(_ label: String, note: String?) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            glanceRow(label, "Unavailable", tint: .secondary)
            if let note {
                Text(note).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private func load(revision: UInt64, resetForSession: Bool = false) async {
        generation &+= 1
        if resetForSession { resetSettledState() }
        guard revision == model.sessionRevision,
            let identity = model.sessionIdentity, let client = model.client
        else {
            currentOwner = nil
            isLoading = false
            return
        }
        let owner = LoadOwner(
            identity: identity, revision: revision, generation: generation)
        currentOwner = owner
        isLoading = true
        defer { if owns(owner) { isLoading = false } }

        do {
            let result = try await client.getHouseholdContext(.init())
            guard owns(owner), !Task.isCancelled else { return }
            guard case .ok(let response) = result else {
                // Usually a 401 from a stale relayed token (ADR 0067 v6):
                // pull the phone's current pairing and try once more.
                let refreshed = await model.requestFreshCredential(
                    expectedIdentity: owner.identity)
                if refreshed { return }  // the revision-keyed task restarts with the new credential
                guard owns(owner), !Task.isCancelled else { return }
                errorMessage = "The box answered unexpectedly."
                return
            }
            let loaded = try response.body.json
            guard owns(owner), !Task.isCancelled else { return }
            context = loaded
            committedOwner = owner
            errorMessage = nil

            // A fresh context owns a fresh set of optional decisions. Replace
            // the cache immediately, before any optional request can hang or be
            // cancelled, so an older exact headline cannot survive this commit.
            plan = nil
            outlook = nil
            budgets = nil
            cacheContextOnlyFaceSnapshot(owner: owner)
        } catch {
            guard owns(owner), !Task.isCancelled else { return }
            errorMessage = "Can't reach the box — check the phone's connection."
            return
        }

        guard owns(owner), !Task.isCancelled else { return }
        let planResult = try? await client.getSpendingPlan(.init())
        guard owns(owner), !Task.isCancelled else { return }
        if case .ok(let response)? = planResult { plan = try? response.body.json }

        let outlookResult = try? await client.getCashOutlook(.init())
        guard owns(owner), !Task.isCancelled else { return }
        if case .ok(let response)? = outlookResult { outlook = try? response.body.json }

        let budgetResult = try? await client.listBudgets(.init())
        guard owns(owner), !Task.isCancelled else { return }
        if case .ok(let response)? = budgetResult { budgets = try? response.body.json }

        cacheFaceSnapshot(owner: owner)
    }

    private func owns(_ owner: LoadOwner) -> Bool {
        currentOwner == owner
            && model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func belongsToCurrentSession(_ owner: LoadOwner?) -> Bool {
        guard let owner else { return false }
        return model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func resetSettledState() {
        currentOwner = nil
        committedOwner = nil
        context = nil
        plan = nil
        outlook = nil
        budgets = nil
        errorMessage = nil
        isLoading = false
    }

    private func completeMinor(_ money: Components.Schemas.QualifiedMoney?) -> Int64? {
        guard let money, !money.isPartial else { return nil }
        return money.amountMinor
    }

    /// Commit the authoritative context primitives while deliberately clearing
    /// every plan/outlook/budget primitive. Optional enrichment may never return.
    private func cacheContextOnlyFaceSnapshot(owner: LoadOwner) {
        guard owns(owner), let context else { return }
        WatchFaceSnapshotStore().save(
            .contextOnly(
                safeToSpendMinor: completeMinor(context.safeToSpend?.safeToSpend),
                netWorthMinor: context.netWorth.amountMinor,
                netWorthIncompleteCount: context.netWorth.incompleteCount,
                currency: context.netWorth.currency))
        guard owns(owner) else { return }
        WidgetCenter.shared.reloadAllTimelines()
    }

    /// Feed the watch-face complication (ADR 0067 v5): cache the enriched glance
    /// numbers to the App Group and nudge the widget to re-read them.
    private func cacheFaceSnapshot(owner: LoadOwner) {
        guard owns(owner), let context else { return }
        WatchFaceSnapshotStore().save(
            WatchFaceSnapshot(
                leftToSpendMinor: completeMinor(plan?.leftToSpend),
                safeToSpendMinor: completeMinor(context.safeToSpend?.safeToSpend),
                lowestBalanceMinor: completeMinor(outlook?.lowestBalance),
                netWorthMinor: context.netWorth.amountMinor,
                monthIncomeMinor: plan?.incomeReceived.amountMinor,
                monthSpendingMinor: plan?.spent.amountMinor,
                expectedIncomeMinor: completeMinor(plan?.expectedIncome),
                netWorthIncompleteCount: context.netWorth.incompleteCount,
                monthIncomeIncompleteCount: plan?.incomeReceived.incompleteCount,
                monthSpendingIncompleteCount: plan?.spent.incompleteCount,
                currency: context.netWorth.currency,
                capturedAt: Date(),
                budgets: budgets.map { WatchFaceSnapshot.slices(from: $0.budgets) },
                budgetedMinor: budgets?.summary.totalBudgeted.amountMinor,
                budgetSpentMinor: budgets?.summary.totalSpent.amountMinor,
                budgetSpentIncompleteCount: budgets?.summary.totalSpent.incompleteCount,
                budgetOverCount: budgets?.summary.overCount,
                budgetWarningCount: budgets?.summary.warningCount))
        guard owns(owner) else { return }
        WidgetCenter.shared.reloadAllTimelines()
    }
}
