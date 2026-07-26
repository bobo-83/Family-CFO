import SwiftUI

/// Every budget on the wrist (ADR 0067 v10): the same limit/usage rows the
/// phone's Budgets screen shows, read-only — edits stay on phone and web.
struct WatchBudgetsView: View {
    @Environment(WatchModel.self) private var model
    @State private var budgets: [Components.Schemas.Budget]?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let budgets {
                    if let summary = summary(budgets) {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text("Budgeted \(summary.limit)")
                                    .font(.footnote.weight(.semibold))
                                Spacer()
                                Text("\(summary.percentUsed)%")
                                    .font(.footnote)
                                    .monospacedDigit()
                                    .foregroundStyle(
                                        summary.percentUsed >= 100
                                            ? .red
                                            : summary.percentUsed >= 80 ? .orange : .secondary)
                            }
                            ProgressView(value: min(Double(summary.percentUsed) / 100, 1))
                                .tint(
                                    summary.percentUsed >= 100
                                        ? .red : summary.percentUsed >= 80 ? .orange : .green)
                            Text("\(summary.spent) spent so far")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Divider()
                    }
                    if budgets.isEmpty {
                        Text("No budgets yet — set them on the phone or web.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(budgets, id: \.id) { budget in
                            row(budget)
                        }
                    }
                } else if let errorMessage {
                    Text(errorMessage).font(.caption2).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // Clear of the page-indicator dots (user report 2026-07-25).
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle("Budgets")
        .task { await load() }
        .refreshable { await load() }
    }

    private func row(_ budget: Components.Schemas.Budget) -> some View {
        let fraction = Double(budget.percentUsed) / 100
        return VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(budget.categoryName).font(.footnote).lineLimit(1)
                Spacer()
                Text("\(budget.percentUsed)%")
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
            ProgressView(value: min(fraction, 1))
                .tint(fraction >= 1 ? .red : fraction >= 0.8 ? .orange : .green)
            Text("\(budget.spent.formatted) of \(budget.limit.formatted)")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    /// Same arithmetic as the phone's BudgetsViewModel.summary (user request
    /// 2026-07-25): total limit, total spent, burn percent.
    private func summary(
        _ budgets: [Components.Schemas.Budget]
    ) -> (limit: String, spent: String, percentUsed: Int)? {
        guard let currency = budgets.first?.limit.currency else { return nil }
        let limit = budgets.reduce(Int64(0)) { $0 + Int64($1.limit.amountMinor) }
        guard limit > 0 else { return nil }
        let spent = budgets.reduce(Int64(0)) { $0 + Int64($1.spent.amountMinor) }
        func money(_ minor: Int64) -> String {
            (Decimal(minor) / 100).formatted(.currency(code: currency).precision(.fractionLength(0)))
        }
        return (
            money(limit), money(spent),
            Int((Double(spent) / Double(limit) * 100).rounded())
        )
    }

    private func load(retried: Bool = false) async {
        guard let client = model.client else { return }
        do {
            guard case .ok(let response) = try await client.listBudgets(.init()) else {
                // Stale relayed token (ADR 0067 v6): refresh from the phone, retry once.
                if !retried, await model.requestFreshCredential() {
                    return await load(retried: true)
                }
                errorMessage = "The box answered unexpectedly."
                return
            }
            budgets = try response.body.json.budgets
            errorMessage = nil
        } catch {
            errorMessage = "Can't reach the box."
        }
    }
}
