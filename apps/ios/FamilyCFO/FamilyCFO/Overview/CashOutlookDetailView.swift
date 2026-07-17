import SwiftUI

/// The day-by-day cash projection behind the outlook card (M112, ADR 0026):
/// every expected paycheck and payment in date order with the running balance
/// beside it, so the lowest point is something you can see coming, not a claim.
struct CashOutlookDetailView: View {
    let outlook: Components.Schemas.CashOutlookResponse

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 4) {
                    Text(outlook.lowestBalance.formatted)
                        .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                        .foregroundStyle(
                            outlook.lowestBalance.amountMinor >= 0 ? Color.primary : .red)
                    Text(
                        outlook.lowestDate.map {
                            "lowest point, \(BillsView.shortDate($0)) — over the next \(outlook.horizonDays) days"
                        } ?? "nothing expected in the next \(outlook.horizonDays) days"
                    )
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }

            Section {
                LabeledContent("Cash today") {
                    Text(outlook.startingCash.formatted).monospacedDigit()
                }
                LabeledContent("Expected paychecks") {
                    Text("+" + outlook.expectedIncome.formatted)
                        .monospacedDigit().foregroundStyle(.green)
                }
                LabeledContent("Payments due") {
                    Text("−" + outlook.obligations.formatted)
                        .monospacedDigit().foregroundStyle(.secondary)
                }
                LabeledContent {
                    Text(outlook.endingCash.formatted)
                        .font(.headline).monospacedDigit()
                        .foregroundStyle(
                            outlook.endingCash.amountMinor >= 0 ? Color.primary : .red)
                } label: {
                    Text("In \(outlook.horizonDays) days").font(.headline)
                }
            } header: {
                Text("The month ahead")
            }

            Section {
                ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                    HStack(spacing: 12) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.event.name).lineLimit(1)
                            Text(BillsView.shortDate(row.event.occurredOn))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(
                                (row.event.amount.amountMinor >= 0 ? "+" : "")
                                    + row.event.amount.formattedExact
                            )
                            .font(.subheadline.weight(.medium))
                            .monospacedDigit()
                            .foregroundStyle(
                                row.event.amount.amountMinor >= 0 ? .green : .primary)
                            Text(row.balance.formattedExact)
                                .font(.caption)
                                .monospacedDigit()
                                .foregroundStyle(
                                    row.balance.amountMinor >= 0 ? Color.secondary : .red)
                        }
                    }
                }
            } header: {
                Text("Day by day")
            } footer: {
                Text(
                    "Paydays come from your recurring deposits; payments from the Bills "
                        + "timeline. Card amounts are today's balances — charges you make "
                        + "between now and the due date aren't known yet, so the real "
                        + "figure may be higher."
                )
            }
        }
        .navigationTitle("Cash outlook")
        .navigationBarTitleDisplayMode(.inline)
    }

    private struct Row {
        let event: Components.Schemas.OutlookEvent
        let balance: Components.Schemas.Money
    }

    /// Events with the running balance after each — same order the server
    /// projected (outflows before inflows on a same day, erring low).
    private var rows: [Row] {
        var running = outlook.startingCash.amountMinor
        return outlook.events.map { event in
            running += event.amount.amountMinor
            return Row(
                event: event,
                balance: .init(amountMinor: running, currency: outlook.startingCash.currency))
        }
    }
}
