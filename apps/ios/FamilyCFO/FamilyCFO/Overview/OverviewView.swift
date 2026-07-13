import Charts
import SwiftUI

/// The daily-glance screen (M88). Read-only: net worth and its trend, the
/// emergency fund against the household's own target, monthly cash flow,
/// upcoming bills, budget alerts, top goal, savings rate — all straight from
/// `GET /household`, the same context the advisor reasons over.
struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: OverviewViewModel?

    var body: some View {
        NavigationStack {
            Group {
                if let viewModel {
                    content(viewModel)
                } else {
                    ContentUnavailableView(
                        "Not paired",
                        systemImage: "iphone.slash",
                        description: Text("Pair this phone with your household's box to see your numbers.")
                    )
                }
            }
            .navigationTitle("Overview")
        }
        .task {
            if viewModel == nil, let api = model.household {
                viewModel = OverviewViewModel(api: api)
            }
            await viewModel?.load()
        }
    }

    @ViewBuilder
    private func content(_ viewModel: OverviewViewModel) -> some View {
        if let errorMessage = viewModel.errorMessage, viewModel.context == nil {
            ContentUnavailableView {
                Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("Retry") { Task { await viewModel.load() } }
                    .buttonStyle(.borderedProminent)
            }
        } else if let context = viewModel.context {
            ScrollView {
                VStack(spacing: 16) {
                    netWorthCard(context)
                    if let fund = context.emergencyFund {
                        emergencyFundCard(fund)
                    }
                    if let cashFlow = context.monthlyCashFlow {
                        cashFlowCard(cashFlow)
                    }
                    if let savingsRate = context.savingsRate {
                        savingsRateCard(savingsRate)
                    }
                    if let budgets = context.budgetSummary, budgets.envelopeCount > 0 {
                        budgetCard(budgets)
                    }
                    if let goal = context.topGoal {
                        goalCard(goal)
                    }
                    if let bills = context.upcomingBills, !bills.isEmpty {
                        upcomingBillsCard(bills)
                    }
                }
                .padding()
            }
            .refreshable { await viewModel.load() }
        } else {
            ProgressView()
        }
    }

    // MARK: Cards

    private func netWorthCard(_ context: Components.Schemas.HouseholdContext) -> some View {
        Card("Net worth", systemImage: "chart.line.uptrend.xyaxis") {
            Text(context.netWorth.formatted)
                .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                .contentTransition(.numericText())
            if let history = context.netWorthHistory, history.count >= 2 {
                sparkline(history)
                    .frame(height: 56)
            }
            if let debt = context.totalDebt, debt.amountMinor > 0 {
                LabeledContent("Total debt", value: debt.formatted)
                    .font(.subheadline)
            }
        }
    }

    /// Index-based x-axis: the snapshots are already oldest-first (M40), and
    /// the shape is the point — no date arithmetic the server didn't do.
    private func sparkline(_ history: [Components.Schemas.NetWorthPoint]) -> some View {
        let rising = (history.last?.netWorth.amountMinor ?? 0)
            >= (history.first?.netWorth.amountMinor ?? 0)
        return Chart(Array(history.enumerated()), id: \.offset) { index, point in
            LineMark(
                x: .value("Snapshot", index),
                y: .value("Net worth", point.netWorth.decimalValue)
            )
            .interpolationMethod(.catmullRom)
            AreaMark(
                x: .value("Snapshot", index),
                y: .value("Net worth", point.netWorth.decimalValue)
            )
            .interpolationMethod(.catmullRom)
            .foregroundStyle(
                .linearGradient(
                    colors: [(rising ? Color.green : .orange).opacity(0.25), .clear],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
        .foregroundStyle(rising ? Color.green : .orange)
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: .automatic(includesZero: false))
        .accessibilityLabel("Net worth trend")
    }

    private func emergencyFundCard(
        _ fund: Components.Schemas.EmergencyFundSummary
    ) -> some View {
        Card("Emergency fund", systemImage: "umbrella") {
            HStack(alignment: .firstTextBaseline) {
                Text(fund.reserved.formatted)
                    .font(.title2.weight(.semibold))
                Spacer()
                Text(fund.statusLabel)
                    .font(.caption.weight(.medium))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(fund.statusTint.opacity(0.18), in: Capsule())
                    .foregroundStyle(fund.statusTint)
            }
            if let progress = fund.progressToRecommended {
                ProgressView(value: progress)
                    .tint(fund.statusTint)
            }
            if let months = fund.months {
                Text(
                    "\(months.formatted(.number.precision(.fractionLength(1)))) of "
                        + "\(fund.targetMonthsRecommended.formatted(.number.precision(.fractionLength(0)))) months' expenses"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            if let gap = fund.gapToRecommended, gap.amountMinor > 0 {
                Text("\(gap.formatted) to the recommended target")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func cashFlowCard(_ flow: Components.Schemas.MonthlyCashFlow) -> some View {
        Card("Monthly cash flow", systemImage: "arrow.left.arrow.right") {
            HStack {
                stat("Income", flow.income.formatted, tint: .green)
                Divider()
                stat("Bills", flow.bills.formatted, tint: .orange)
                Divider()
                stat(
                    "Net", flow.net.formatted,
                    tint: flow.net.amountMinor >= 0 ? .green : .red)
            }
        }
    }

    private func savingsRateCard(_ rate: Components.Schemas.SavingsRate) -> some View {
        Card("Savings rate", systemImage: "banknote") {
            if let percent = rate.percent {
                Text("\(percent)%")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(percent >= 0 ? Color.green : .red)
            } else {
                Text("—").font(.title2.weight(.semibold))
            }
            Text(
                "\(rate.monthlyIncome.formatted) income vs "
                    + "\(rate.averageMonthlySpending.formatted) average spending"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
    }

    private func budgetCard(_ budgets: Components.Schemas.BudgetSummary) -> some View {
        Card("Budgets", systemImage: "chart.pie") {
            HStack {
                stat("Envelopes", "\(budgets.envelopeCount)", tint: .secondary)
                Divider()
                stat("Over", "\(budgets.overCount)", tint: budgets.overCount > 0 ? .red : .secondary)
                Divider()
                stat(
                    "Warning", "\(budgets.warningCount)",
                    tint: budgets.warningCount > 0 ? .orange : .secondary)
            }
            Text("\(budgets.totalSpent.formatted) spent of \(budgets.totalBudgeted.formatted)")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func goalCard(_ goal: Components.Schemas.GoalProgress) -> some View {
        Card("Top goal", systemImage: "target") {
            Text(goal.name).font(.headline)
            ProgressView(value: Double(goal.percentComplete), total: 100)
            Text("\(goal.current.formatted) of \(goal.target.formatted) · \(goal.percentComplete)%")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func upcomingBillsCard(
        _ bills: [Components.Schemas.UpcomingBill]
    ) -> some View {
        Card("Due soon", systemImage: "calendar") {
            ForEach(bills, id: \.id) { bill in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(bill.name).font(.subheadline)
                        Text(Self.dueDescription(daysUntil: bill.daysUntil))
                            .font(.caption)
                            .foregroundStyle(bill.daysUntil <= 3 ? Color.orange : .secondary)
                    }
                    Spacer()
                    Text(bill.amount.formattedExact)
                        .font(.subheadline.weight(.medium))
                }
                if bill.id != bills.last?.id {
                    Divider()
                }
            }
        }
    }

    static func dueDescription(daysUntil: Int) -> String {
        switch daysUntil {
        case ..<0: return "Overdue"
        case 0: return "Due today"
        case 1: return "Due tomorrow"
        default: return "Due in \(daysUntil) days"
        }
    }

    private func stat(_ label: String, _ value: String, tint: Color) -> some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(tint)
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }
}

extension Components.Schemas.EmergencyFundSummary {
    var statusTint: Color {
        switch status {
        case .fullyFunded, .onTrack: return .green
        case .gettingStarted: return .orange
        case .noFund: return .red
        case .noBills: return .secondary
        }
    }
}

/// A titled card. Every Overview section is one, so the screen reads as a stack
/// of equals rather than a hierarchy the data doesn't have.
private struct Card<Content: View>: View {
    let title: String
    let systemImage: String
    @ViewBuilder let content: Content

    init(_ title: String, systemImage: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.systemImage = systemImage
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label(title, systemImage: systemImage)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.fill.quinary, in: RoundedRectangle(cornerRadius: 16))
    }
}
