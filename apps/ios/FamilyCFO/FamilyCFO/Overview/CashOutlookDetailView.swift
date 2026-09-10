import SwiftUI

/// The cash projection behind the outlook card. Running balances are shown only
/// when every server projection decision is available; readable components remain
/// visible when the projection is unavailable.
struct CashOutlookDetailView: View {
    let outlook: Components.Schemas.CashOutlookResponse

    private var projectionAvailable: Bool {
        guard let lowest = outlook.lowestBalance,
            let ending = outlook.endingCash,
            let expected = outlook.expectedIncome
        else { return false }
        return !lowest.isPartial && !ending.isPartial && !expected.isPartial
    }

    var body: some View {
        List {
            if let lowest = outlook.lowestBalance {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(verbatim: lowest.formatted)
                            .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                            .foregroundStyle(lowest.isPartial ? Color.primary : (lowest.amountMinor >= 0 ? .primary : .red))
                            .accessibilityLabel(lowest.accessibilityDescription)
                        if let note = lowest.partialDisclosure {
                            Text(note).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(
                            outlook.lowestDate.map {
                                String(
                                    localized:
                                        "lowest point, \(BillsView.shortDate($0)) — over the next \(outlook.horizonDays) days")
                            } ?? String(localized: "nothing expected in the next \(outlook.horizonDays) days")
                        )
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 4)
                }
            } else {
                Section {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Projection unavailable")
                            .font(.headline)
                            .foregroundStyle(.secondary)
                        if let note = outlook.incomeProjection.unavailableDisclosure {
                            Text(note).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }

            Section {
                LabeledContent("Cash today") {
                    Text(verbatim: outlook.startingCash.formatted).monospacedDigit()
                }
                qualifiedRow("Expected paychecks", outlook.expectedIncome, prefix: "+")
                qualifiedRow("Payments due", outlook.obligations, prefix: "−")
                qualifiedRow("In \(outlook.horizonDays) days", outlook.endingCash, emphasized: true)
            } header: {
                Text("The month ahead")
            }

            if projectionAvailable {
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
                                        + row.event.amount.formattedExact)
                                    .font(.subheadline.weight(.medium))
                                    .monospacedDigit()
                                    .foregroundStyle(row.event.amount.amountMinor >= 0 ? .green : .primary)
                                if let note = Self.statementNote(row.event) {
                                    Label(note, systemImage: "doc.text.fill")
                                        .font(.caption2.weight(.medium))
                                        .foregroundStyle(.teal)
                                }
                                Text(verbatim: row.balance.formattedExact)
                                    .font(.caption)
                                    .monospacedDigit()
                                    .foregroundStyle(row.balance.amountMinor >= 0 ? Color.secondary : .red)
                            }
                        }
                    }
                } header: {
                    Text("Day by day")
                } footer: {
                    Text(
                        String(
                            localized: """
                                Paydays come from your recurring deposits; payments from the Bills \
                                timeline. Rows marked from statement are exact; the rest are \
                                estimates — a card without a statement shows today's balance, \
                                which may be lower than the final figure.
                                """))
                }
            }
        }
        .navigationTitle("Cash outlook")
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private func qualifiedRow(
        _ label: String,
        _ money: Components.Schemas.QualifiedMoney?,
        prefix: String = "",
        emphasized: Bool = false
    ) -> some View {
        LabeledContent {
            if let money {
                VStack(alignment: .trailing, spacing: 2) {
                    Text(verbatim: prefix + money.formatted)
                        .font(emphasized ? .headline : .body)
                        .monospacedDigit()
                    if let note = money.partialDisclosure {
                        Text(note).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel(money.accessibilityDescription)
            } else {
                Text("Unavailable").foregroundStyle(.secondary)
            }
        } label: {
            Text(label).font(emphasized ? .headline : .body)
        }
    }

    private struct Row {
        let event: Components.Schemas.OutlookEvent
        let balance: Components.Schemas.Money
    }

    static func statementNote(_ event: Components.Schemas.OutlookEvent) -> String? {
        event.source == "statement" ? String(localized: "Exact — from your statement") : nil
    }

    /// Only called for an available projection; never rebuild a missing decision
    /// from a partial event stream.
    private var rows: [Row] {
        guard projectionAvailable else { return [] }
        var running = outlook.startingCash.amountMinor
        return outlook.events.map { event in
            running += event.amount.amountMinor
            return Row(
                event: event,
                balance: .init(amountMinor: running, currency: outlook.startingCash.currency))
        }
    }
}
