import SwiftUI

/// The Overview, wrist-sized: safe-to-spend leads (the number the family
/// actually acts on), then net worth and this month's flow — all straight
/// from the same `GET /household` context every other client renders.
struct WatchGlanceView: View {
    @Environment(WatchModel.self) private var model
    @State private var context: Components.Schemas.HouseholdContext?
    @State private var errorMessage: String?
    @State private var isLoading = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if let context {
                    if let sts = context.safeToSpend {
                        glanceRow(
                            "Safe to spend", sts.safeToSpend.formatted,
                            tint: sts.safeToSpend.amountMinor >= 0 ? .green : .red)
                    }
                    glanceRow("Net worth", context.netWorth.formatted, tint: .primary)
                    if let flow = context.monthlyCashFlow {
                        glanceRow("In / month", flow.income.formatted, tint: .green)
                        glanceRow("Bills / month", flow.bills.formatted, tint: .orange)
                    }
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

    private func load() async {
        guard let client = model.client else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            guard case .ok(let response) = try await client.getHouseholdContext(.init()) else {
                errorMessage = "The box answered unexpectedly."
                return
            }
            context = try response.body.json
            errorMessage = nil
        } catch {
            errorMessage = "Can't reach the box — check the phone's connection."
        }
    }
}
