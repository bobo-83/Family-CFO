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
                    Text(verbatim: outlook.lowestBalance.formatted)
                        .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                        .foregroundStyle(
                            outlook.lowestBalance.amountMinor >= 0 ? Color.primary : .red)
                    Text(
                        outlook.lowestDate.map {
                            String(
                                localized:
                                    "lowest point, \(BillsView.shortDate($0)) — over the next \(outlook.horizonDays) days"
                            )
                        }
                            ?? String(
                                localized:
                                    "nothing expected in the next \(outlook.horizonDays) days")
                    )
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }

            Section {
                LabeledContent("Cash today") {
                    Text(verbatim: outlook.startingCash.formatted).monospacedDigit()
                }
                LabeledContent("Expected paychecks") {
                    Text(verbatim: "+" + outlook.expectedIncome.formatted)
                        .monospacedDigit().foregroundStyle(.green)
                }
                LabeledContent("Payments due") {
                    Text(verbatim: "−" + outlook.obligations.formatted)
                        .monospacedDigit().foregroundStyle(.secondary)
                }
                LabeledContent {
                    Text(verbatim: outlook.endingCash.formatted)
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
                            Text(verbatim: row.event.name).lineLimit(1)
                            Text(verbatim: BillsView.shortDate(row.event.occurredOn))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        VStack(alignment: .trailing, spacing: 2) {
                            Text(
                                verbatim: (row.event.amount.amountMinor >= 0 ? "+" : "")
                                    + row.event.amount.formattedExact
                            )
                            .font(.subheadline.weight(.medium))
                            .monospacedDigit()
                            .foregroundStyle(
                                row.event.amount.amountMinor >= 0 ? .green : .primary)
                            if let note = Self.statementNote(row.event) {
                                Label(note, systemImage: "doc.text.fill")
                                    .font(.caption2.weight(.medium))
                                    .foregroundStyle(.teal)
                            }
                            Text(verbatim: row.balance.formattedExact)
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
                // #30: the rows now say which figures are exact, so this points
                // at the badge instead of hedging about every card.
                Text(
                    String(
                        localized: """
                            Paydays come from your recurring deposits; payments from the Bills \
                            timeline. Rows marked from statement are exact; the rest are \
                            estimates — a card without a statement shows today's balance, \
                            which may be lower than the final figure.
                            """)
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

    /// #30: only a statement-backed row carries the EXACT figure the issuer
    /// billed. Everything else — a card's running balance on an inferred due
    /// day, any projected future occurrence, every bill, loan and payday — is
    /// an estimate and gets no badge. Same wording and idiom as the Bills
    /// timeline (`BillsView.statementNote`) so the two screens agree.
    static func statementNote(_ event: Components.Schemas.OutlookEvent) -> String? {
        event.source == "statement" ? String(localized: "Exact — from your statement") : nil
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
