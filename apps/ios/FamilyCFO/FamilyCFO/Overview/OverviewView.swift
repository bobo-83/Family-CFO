import Charts
import SwiftUI

/// The daily-glance screen (M88). Read-only: net worth and its trend, the
/// emergency fund against the household's own target, monthly cash flow,
/// upcoming bills, budget alerts, top goal, savings rate — all straight from
/// `GET /household`, the same context the advisor reasons over.
struct OverviewView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: OverviewViewModel?
    // M-yearly: the Overview flips between the month glance and the year trend.
    @State private var viewMode: ViewMode = .month
    @State private var yearlyModel: YearlyOverviewViewModel?

    enum ViewMode: String, CaseIterable {
        case month = "Month"
        case year = "Year"
    }

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
            if yearlyModel == nil, let api = model.household {
                yearlyModel = YearlyOverviewViewModel(api: api)
            }
            await viewModel?.load()
            // Seed the shared freshness clock so every tab agrees (M103).
            model.syncStatus.observe(viewModel?.context?.lastSyncedAt)
            if let month = viewModel?.selectedMonth { await warmMonthCache(month) }
        }
    }

    /// Load the month's transactions + categories into the shared cache (M105) so
    /// spending drill-downs read from memory. This is the one explicit fetch —
    /// triggered by Overview loading or a pull-to-refresh, not by drilling in.
    private func warmMonthCache(_ month: String) async {
        guard let household = model.household, let categorize = model.categorize else { return }
        await model.monthTransactions.reload(
            month: month,
            transactions: { try await household.transactions(month: month) },
            categories: { try await categorize.categories() })
    }

    /// The Overview-wide month selector (M96): step the whole page back through
    /// history. Next is disabled at the current month.
    private func monthPicker(_ viewModel: OverviewViewModel) -> some View {
        HStack {
            Button {
                Task {
                    await viewModel.shiftMonth(-1)
                    await warmMonthCache(viewModel.selectedMonth)
                }
            } label: {
                Image(systemName: "chevron.left").font(.headline)
            }
            .disabled(!viewModel.canGoBack || viewModel.isLoading)
            Spacer()
            HStack(spacing: 6) {
                if viewModel.isLoading {
                    ProgressView()
                }
                Text(viewModel.monthLabel).font(.headline)
            }
            Spacer()
            Button {
                Task {
                    await viewModel.shiftMonth(1)
                    await warmMonthCache(viewModel.selectedMonth)
                }
            } label: {
                Image(systemName: "chevron.right").font(.headline)
            }
            .disabled(viewModel.isCurrentMonth || viewModel.isLoading)
        }
        .padding(.horizontal, 4)
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
                    Picker("View", selection: $viewMode) {
                        ForEach(ViewMode.allCases, id: \.self) { mode in
                            Text(mode.rawValue).tag(mode)
                        }
                    }
                    .pickerStyle(.segmented)
                    if viewMode == .year {
                        if let yearlyModel {
                            YearlyOverviewView(viewModel: yearlyModel) { month in
                                // Drill-down: jump the whole Overview to that month.
                                viewMode = .month
                                Task {
                                    await viewModel.show(month: month)
                                    await warmMonthCache(month)
                                }
                            }
                        }
                    } else {
                    monthPicker(viewModel)
                    // M120 (ADR 0029): the box and the app ship one monorepo
                    // version - say so loudly when they have drifted apart.
                    if viewModel.versionMismatch, let server = viewModel.serverVersion {
                        versionMismatchBanner(server: server)
                    }
                    if !viewModel.isCurrentMonth {
                        Text("Historical view of \(viewModel.monthLabel). “Right now” figures like safe-to-spend and upcoming bills only appear for the current month.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    // M112 (ADR 0026): the lived cash picture leads — the same
                    // due-vs-cash verdict as the Bills tab, plus the 30-day
                    // projection with paychecks counted.
                    if let outlook = viewModel.outlook {
                        cashOutlookCard(outlook)
                    }
                    // M113 (ADR 0027): the month plan — income vs spent vs committed.
                    if let plan = viewModel.plan {
                        spendingPlanCard(plan)
                    }
                    if let sts = context.safeToSpend {
                        safeToSpendCard(sts, context.upcomingBills ?? [])
                    }
                    // Spending-by-category sits high: it's the freshest result of
                    // the user's categorizing, and the thing they came to see.
                    if let spending = context.spendingByCategory,
                        !(spending.categories ?? []).isEmpty,
                        let api = model.household, let categorize = model.categorize {
                        SpendingCard(
                            spending: spending, api: api, categorizeAPI: categorize,
                            onChanged: { await viewModel.reload() })
                    }
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
                    }  // viewMode == .month
                }
                .padding()
            }
            // Pull-to-refresh runs the bank sync, same as every other tab, and
            // re-warms the drill-down cache (M105) so it reflects the new data.
            .refreshable {
                await viewModel.syncNow()
                model.syncStatus.markSynced()
                model.monthTransactions.invalidate()
                await warmMonthCache(viewModel.selectedMonth)
            }
            .safeAreaInset(edge: .bottom) {
                SyncStatusFooter(status: model.syncStatus)
                    .padding(.vertical, 6)
            }
        } else {
            ProgressView()
        }
    }

    /// M120: the app is stale (or the box is) - point at the OTA page.
    private func versionMismatchBanner(server: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(
                "App v\(OverviewViewModel.appVersion) · box v\(server)",
                systemImage: "exclamationmark.arrow.triangle.2.circlepath"
            )
            .font(.subheadline.weight(.semibold))
            Text(
                "Versions differ, so screens may not match the server. "
                    + "Install the update from your box's OTA page."
            )
            .font(.caption)
            if let base = model.server?.apiBaseURL,
                let ota = URL(string: "/ota/", relativeTo: base) {
                Link("Open the install page", destination: ota)
                    .font(.caption.weight(.semibold))
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.orange.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
        .foregroundStyle(.orange)
    }

    // MARK: Cards

    /// M112 (ADR 0026): the lived cash picture — 30 days of paychecks and
    /// payments with the lowest point the balance reaches. The verdict tracks
    /// that 30-day projection's own lowest point, NOT the 14-day `dueSoon`
    /// check: a payment 15–30 days out (e.g. a big credit-card statement) fell
    /// outside the 14-day window, so the card could read "covered ✓" while the
    /// math below projected the balance thousands negative.
    private func cashOutlookCard(_ outlook: Components.Schemas.CashOutlookResponse) -> some View {
        let staysPositive = outlook.lowestBalance.amountMinor >= 0
        return NavigationLink {
            CashOutlookDetailView(outlook: outlook)
        } label: {
            Card("Cash outlook", systemImage: "calendar.badge.clock") {
                Label(
                    staysPositive
                        ? "Your cash stays positive over the next \(outlook.horizonDays) days"
                        : "Your cash runs short over the next \(outlook.horizonDays) days",
                    systemImage: staysPositive
                        ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
                )
                .font(.subheadline.weight(.medium))
                .foregroundStyle(staysPositive ? .green : .orange)
                Text(outlook.lowestBalance.formatted)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .foregroundStyle(outlook.lowestBalance.amountMinor >= 0 ? Color.primary : .red)
                Text(
                    outlook.lowestDate.map {
                        "lowest your cash reaches in the next \(outlook.horizonDays) days"
                            + " · \(BillsView.shortDate($0))"
                    } ?? "no payments or paydays expected in the next \(outlook.horizonDays) days"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(
                    "\(outlook.startingCash.formatted) cash + \(outlook.expectedIncome.formatted) "
                        + "expected paychecks − \(outlook.obligations.formatted) payments "
                        + "= \(outlook.endingCash.formatted)"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                HStack(spacing: 3) {
                    Text("Tap for the day-by-day projection")
                    Image(systemName: "chevron.right")
                }
                .font(.caption2.weight(.medium))
                .foregroundStyle(.tint)
                .padding(.top, 2)
            }
        }
        .buttonStyle(.plain)
    }

    /// M113 (ADR 0027): left to spend this month — expected income minus what's
    /// already spent and what's still committed. The accrual counterpart to the
    /// cash outlook's cash-timing view.
    private func spendingPlanCard(_ plan: Components.Schemas.SpendingPlanResponse) -> some View {
        Card("Left to spend this month", systemImage: "chart.pie") {
            Text(plan.leftToSpend.formatted)
                .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                .foregroundStyle(plan.leftToSpend.amountMinor >= 0 ? Color.primary : .red)
            if plan.leftToSpend.amountMinor >= 0 {
                Text(
                    "about \(plan.perDay.formatted)/day for the remaining "
                        + "\(plan.daysRemaining) day\(plan.daysRemaining == 1 ? "" : "s")"
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            } else {
                Text(
                    "this month's spending has outrun this month's income — "
                        + "the gap is drawing on cash you already had"
                )
                .font(.caption)
                .foregroundStyle(.orange)
            }
            Text(Self.planEquation(plan))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    /// The plan's equation, built in plain string pieces — a single interpolated
    /// expression here is too much for the type checker.
    static func planEquation(_ plan: Components.Schemas.SpendingPlanResponse) -> String {
        var parts: [String] = []
        let income = plan.expectedIncome.formatted
        let received = plan.incomeReceived.formatted
        let toCome = plan.incomeProjected.formatted
        parts.append("\(income) expected income (\(received) received + \(toCome) to come)")
        parts.append("\(plan.spent.formatted) spent")
        parts.append("\(plan.billsRemaining.formatted) bills still due")
        parts.append("\(plan.accountObligations.formatted) loan & lease payments")
        if plan.plannedSavings.amountMinor > 0 {
            parts.append("\(plan.plannedSavings.formatted) planned savings")
        }
        return parts.joined(separator: " − ")
    }

    /// M93, reframed by M112: the zero-income worst case. The cash outlook above
    /// answers "can I spend?"; this answers "what if every commitment were called
    /// today and no paycheck ever arrived?" — deliberately harsher.
    private func safeToSpendCard(
        _ sts: Components.Schemas.SafeToSpend,
        _ upcomingBills: [Components.Schemas.UpcomingBill]
    ) -> some View {
        NavigationLink {
            SafeToSpendDetailView(safeToSpend: sts, upcomingBills: upcomingBills)
        } label: {
            Card("Stress test", systemImage: "shield.lefthalf.filled") {
                Text(sts.safeToSpend.formatted)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .foregroundStyle(sts.safeToSpend.amountMinor >= 0 ? Color.primary : .red)
                Text(
                    "If every commitment were called today — full card balances, all "
                        + "bills, the emergency fund held back — with no paycheck counted. "
                        + "Deliberately worst-case; the cash outlook above counts income."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(
                    "\(sts.liquidBalance.formatted) liquid − \(sts.emergencyFundReserved.formatted) "
                        + "emergency fund − \(sts.billsDue.formatted) bills − "
                        + "\(sts.minimumDebtPayments.formatted) min. debt"
                        + ((sts.creditCardPayments?.value1).map { " − \($0.formatted) cards" } ?? "")
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                if sts.totalDebt.amountMinor > 0 {
                    LabeledContent("Total debt", value: sts.totalDebt.formatted)
                        .font(.subheadline)
                        .foregroundStyle(.orange)
                }
                ForEach(sts.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
                HStack(spacing: 3) {
                    Text("Tap to see how this is calculated")
                    Image(systemName: "chevron.right")
                }
                .font(.caption2.weight(.medium))
                .foregroundStyle(.tint)
                .padding(.top, 2)
            }
        }
        .buttonStyle(.plain)
    }

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
            // Income is actual money in (net take-home). Show the W2 gross as a
            // labelled baseline for context — they differ because tax and 401(k)
            // are withheld before pay lands.
            if let baseline = flow.incomeBaseline?.value1 {
                Text("Actual take-home; \(baseline.formatted)/mo W-2 gross baseline")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let taxes = flow.taxes?.value1 {
                Text("Taxes withheld: \(taxes.formatted)/mo (RSU & payroll), tracked separately")
                    .font(.caption)
                    .foregroundStyle(.secondary)
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


    /// M118: the summary card now opens the full envelope manager (parity with
    /// the dashboard's Budgets page).
    @ViewBuilder
    private func budgetCard(_ budgets: Components.Schemas.BudgetSummary) -> some View {
        let card = Card("Budgets", systemImage: "chart.pie") {
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
            HStack(spacing: 3) {
                Text("Tap to manage envelopes")
                Image(systemName: "chevron.right")
            }
            .font(.caption2.weight(.medium))
            .foregroundStyle(.tint)
            .padding(.top, 2)
        }
        if let api = model.budgetsAPI {
            NavigationLink {
                BudgetsView(viewModel: BudgetsViewModel(api: api))
            } label: {
                card
            }
            .buttonStyle(.plain)
        } else {
            card
        }
    }

    /// M119: the summary card opens the full goal manager (parity with the
    /// dashboard's Goals page).
    @ViewBuilder
    private func goalCard(_ goal: Components.Schemas.GoalProgress) -> some View {
        let card = Card("Top goal", systemImage: "target") {
            Text(goal.name).font(.headline)
            ProgressView(value: Double(goal.percentComplete), total: 100)
            Text("\(goal.current.formatted) of \(goal.target.formatted) · \(goal.percentComplete)%")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(spacing: 3) {
                Text("Tap to manage goals")
                Image(systemName: "chevron.right")
            }
            .font(.caption2.weight(.medium))
            .foregroundStyle(.tint)
            .padding(.top, 2)
        }
        if let api = model.goalsAPI {
            NavigationLink {
                GoalsView(viewModel: GoalsViewModel(api: api))
            } label: {
                card
            }
            .buttonStyle(.plain)
        } else {
            card
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
struct Card<Content: View>: View {
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
