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
    @State private var budgets: [Components.Schemas.Budget]?
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let context {
                    // EXACTLY the phone Overview's headline figures, in its
                    // order — same fields, same server numbers (user report
                    // 2026-07-25: the wrist showed different numbers because
                    // it read the recurring cash-flow MODEL, not the plan).
                    if let outlook {
                        glanceRow(
                            "30-day low", outlook.lowestBalance.formatted,
                            tint: outlook.lowestBalance.amountMinor >= 0 ? .green : .red)
                    }
                    if let plan {
                        glanceRow(
                            "Left to spend", plan.leftToSpend.formatted,
                            tint: plan.leftToSpend.amountMinor >= 0 ? .green : .red)
                    }
                    if let sts = context.safeToSpend {
                        NavigationLink {
                            WatchSafeToSpendDetail(safeToSpend: sts)
                        } label: {
                            glanceRow(
                                "Safe to spend", sts.safeToSpend.formatted,
                                tint: sts.safeToSpend.amountMinor >= 0 ? .green : .red)
                        }
                        .buttonStyle(.plain)
                    }
                    NavigationLink {
                        WatchNetWorthDetail(context: context)
                    } label: {
                        glanceRow("Net worth", context.netWorth.formatted, tint: .primary)
                    }
                    .buttonStyle(.plain)
                    if let fund = context.emergencyFund, let months = fund.months {
                        glanceRow(
                            "Emergency fund",
                            months.formatted(.number.precision(.fractionLength(1))) + " mo",
                            tint: months < 3 ? .orange : .primary)
                    }
                } else if isLoading {
                    ProgressView()
                } else if let errorMessage {
                    Text(errorMessage).font(.footnote).foregroundStyle(.red)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // Clear of the page-indicator dots (user report 2026-07-25).
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle(model.householdName ?? "Overview")
        .task { await load() }
        .refreshable { await load() }
    }

    private func glanceRow(_ label: String, _ value: String, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).foregroundStyle(tint)
        }
    }

    private func load(retried: Bool = false) async {
        guard let client = model.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            guard case .ok(let response) = try await client.getHouseholdContext(.init()) else {
                // Usually a 401 from a stale relayed token (ADR 0067 v6):
                // pull the phone's current pairing and try once more.
                if !retried, await model.requestFreshCredential() {
                    return await load(retried: true)
                }
                errorMessage = "The box answered unexpectedly."
                return
            }
            context = try response.body.json
            errorMessage = nil
        } catch {
            errorMessage = "Can't reach the box — check the phone's connection."
            return
        }
        // The phone's lead cards; both degrade gracefully like on the phone.
        if case .ok(let response)? = try? await client.getSpendingPlan(.init()) {
            plan = try? response.body.json
        }
        if case .ok(let response)? = try? await client.getCashOutlook(.init()) {
            outlook = try? response.body.json
        }
        // Budgets feed the budget complications only (ADR 0067 v9).
        if case .ok(let response)? = try? await client.listBudgets(.init()) {
            budgets = (try? response.body.json)?.budgets
        }
        cacheFaceSnapshot()
    }

    /// Feed the watch-face complication (ADR 0067 v5): cache the fresh glance
    /// numbers to the App Group and nudge the widget to re-read them.
    private func cacheFaceSnapshot() {
        guard let context else { return }
        WatchFaceSnapshotStore().save(
            WatchFaceSnapshot(
                leftToSpendMinor: plan.map { Int64($0.leftToSpend.amountMinor) },
                safeToSpendMinor: context.safeToSpend.map { Int64($0.safeToSpend.amountMinor) },
                lowestBalanceMinor: outlook.map { Int64($0.lowestBalance.amountMinor) },
                netWorthMinor: Int64(context.netWorth.amountMinor),
                monthIncomeMinor: plan.map { Int64($0.incomeReceived.amountMinor) },
                monthSpendingMinor: plan.map { Int64($0.spent.amountMinor) },
                expectedIncomeMinor: plan.map { Int64($0.expectedIncome.amountMinor) },
                currency: context.netWorth.currency,
                capturedAt: Date(),
                budgets: budgets.map(WatchFaceSnapshot.slices(from:))))
        WidgetCenter.shared.reloadAllTimelines()
    }
}
