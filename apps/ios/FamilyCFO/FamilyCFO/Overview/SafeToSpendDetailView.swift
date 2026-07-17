import SwiftUI

/// The "show your work" behind the Safe-to-spend headline (M96). Every figure is
/// the server's — this view only lays out the components it already sent, so the
/// arithmetic the user sees here is exactly what produced the number.
struct SafeToSpendDetailView: View {
    let safeToSpend: Components.Schemas.SafeToSpend
    let upcomingBills: [Components.Schemas.UpcomingBill]

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 4) {
                    Text(safeToSpend.safeToSpend.formatted)
                        .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                        .foregroundStyle(safeToSpend.safeToSpend.amountMinor >= 0 ? Color.primary : .red)
                    // M112 (ADR 0026): named for what it is — the zero-income
                    // worst case, not a spending allowance.
                    Text("stress test: if every commitment were called today, with no income counted")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }

            Section {
                expandable(
                    "Liquid assets", safeToSpend.liquidBalance, sign: .plus,
                    note: "Cash in checking & savings",
                    items: (safeToSpend.liquidAccounts ?? []).map { ($0.name, $0.balance) }
                )
                expandable(
                    "Emergency fund", safeToSpend.emergencyFundReserved, sign: .minus,
                    note: "Reserved, not for spending",
                    items: (safeToSpend.emergencyFundItems ?? []).map { ($0.name, $0.amount) }
                )
                expandable(
                    "Bills due soon", safeToSpend.billsDue, sign: .minus,
                    note: "Committed over the next month",
                    items: (safeToSpend.billItems ?? []).map { ($0.name, $0.amount) }
                )
                expandable(
                    "Minimum debt payments", safeToSpend.minimumDebtPayments, sign: .minus,
                    note: "The minimums you must pay",
                    items: (safeToSpend.minimumDebtItems ?? []).map { ($0.name, $0.amount) }
                )
                if let cards = committedCards {
                    expandable(
                        "Credit card balances", cards, sign: .minus,
                        note: "Full balance — paid in full monthly",
                        items: (safeToSpend.creditCardItems ?? []).map { ($0.name, $0.amount) }
                    )
                }
                if let subscriptions = committedSubscriptions {
                    expandable(
                        "Recurring subscriptions", subscriptions, sign: .minus,
                        note: "Next charge due within the month",
                        items: (safeToSpend.subscriptionForecastItems ?? []).map { ($0.name, $0.amount) }
                    )
                }
                Divider()
                totalRow("Safe to spend", safeToSpend.safeToSpend)
            } header: {
                Text("How it's calculated")
            } footer: {
                Text("Tap a row with a chevron to see what's behind it.")
            }

            if let cards = committedCards {
                Section("Credit cards") {
                    Text(
                        "You pay your cards in full, so their whole \(cards.formatted) balance "
                            + "is counted as committed above — money about to leave your cash, "
                            + "not long-term debt."
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    Label {
                        Text(
                            "Your bank doesn't send the statement due date, so we count the full "
                                + "current balance — including recent charges that aren't due yet. "
                                + "That's deliberate: it keeps this figure conservative, so it "
                                + "never tells you more is free than really is."
                        )
                    } icon: {
                        Image(systemName: "info.circle")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }

            if !safeToSpend.warnings.isEmpty {
                Section("Heads up") {
                    ForEach(safeToSpend.warnings, id: \.self) { warning in
                        Label(warning, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
            }

            Section {
                Text(
                    "This is a deliberate worst case: your liquid cash minus everything "
                        + "already spoken for — the emergency fund, bills coming due, debt "
                        + "payments, and your full card balances — counting no incoming "
                        + "paychecks at all. A negative number here doesn't mean you can't "
                        + "pay your bills; the Cash outlook on the Overview answers that, "
                        + "with income counted. This number is the cushion you'd have if "
                        + "everything went wrong at once."
                )
                .font(.footnote)
                .foregroundStyle(.secondary)
            } header: {
                Text("How this works")
            }
        }
        .navigationTitle("Safe to spend")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// The full card balance being committed, when the household pays in full.
    private var committedCards: Components.Schemas.Money? {
        guard let cards = safeToSpend.creditCardPayments?.value1, cards.amountMinor > 0 else {
            return nil
        }
        return cards
    }

    /// Recurring subscriptions' next in-window charge (M109), reserved the bill way.
    private var committedSubscriptions: Components.Schemas.Money? {
        guard let subs = safeToSpend.subscriptionForecast?.value1, subs.amountMinor > 0 else {
            return nil
        }
        return subs
    }

    /// A calculation row that expands to show the items behind it, or a plain row
    /// when there's nothing to break down.
    @ViewBuilder
    private func expandable(
        _ label: String, _ money: Components.Schemas.Money, sign: Sign, note: String,
        items: [(String, Components.Schemas.Money)]
    ) -> some View {
        if items.isEmpty {
            componentRow(label, money, sign: sign, note: note)
        } else {
            DisclosureGroup {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    LabeledContent(item.0) {
                        Text(item.1.formatted).foregroundStyle(.secondary).monospacedDigit()
                    }
                    .font(.subheadline)
                }
            } label: {
                componentRow(label, money, sign: sign, note: note)
            }
        }
    }

    private static func billLabel(_ bill: Components.Schemas.UpcomingBill) -> String {
        "\(bill.name) · \(due(bill))"
    }

    private enum Sign { case plus, minus }

    private func componentRow(
        _ label: String, _ money: Components.Schemas.Money, sign: Sign, note: String
    ) -> some View {
        LabeledContent {
            Text((sign == .plus ? "" : "−") + money.formatted)
                .monospacedDigit()
                .foregroundStyle(sign == .plus ? Color.primary : .secondary)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                Text(note).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func totalRow(_ label: String, _ money: Components.Schemas.Money) -> some View {
        LabeledContent {
            Text(money.formatted)
                .font(.headline)
                .monospacedDigit()
                .foregroundStyle(money.amountMinor >= 0 ? Color.primary : .red)
        } label: {
            Text(label).font(.headline)
        }
    }

    private static func due(_ bill: Components.Schemas.UpcomingBill) -> String {
        switch bill.daysUntil {
        case ..<0: return "overdue"
        case 0: return "due today"
        case 1: return "due tomorrow"
        default: return "due in \(bill.daysUntil) days"
        }
    }
}
