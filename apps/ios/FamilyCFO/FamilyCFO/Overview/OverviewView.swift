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
    // #203: the declare-a-contribution sheet.
    @State private var declaringSavings = false

    /// #203: declaring, stopping and dismissing all take transactions.manage,
    /// the same right the box requires — a viewer sees the rows, not the edits.
    private var canManageSavings: Bool { model.rolePolicy.canCategorize }

    enum ViewMode: String, CaseIterable {
        case month = "Month"
        case year = "Year"

        /// The raw value is the stable key; this is what the picker shows.
        var label: String {
            switch self {
            case .month: return String(localized: "Month")
            case .year: return String(localized: "Year")
            }
        }
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
                viewModel = OverviewViewModel(api: api, goalsAPI: model.goalsAPI)
            }
            if yearlyModel == nil, let api = model.household {
                yearlyModel = YearlyOverviewViewModel(api: api)
            }
            await viewModel?.load()
            // Seed the shared freshness clock so every tab agrees (M103).
            model.syncStatus.observe(viewModel?.context?.lastSyncedAt)
            if let month = viewModel?.selectedMonth { await warmMonthCache(month) }
        }
        // The household language rides along automatically: every live context
        // fetch seeds AppModel.householdLanguage inside LiveHouseholdAPI (#10).
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
                Text(verbatim: viewModel.monthLabel).font(.headline)
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
                            Text(mode.label).tag(mode)
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
                    // A failed edit (#203's declare/stop/dismiss, a sync) has to
                    // say so: the numbers below are still the last good load, so
                    // nothing else on the page would look any different.
                    if let errorMessage = viewModel.errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.red)
                            .frame(maxWidth: .infinity, alignment: .leading)
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
                    // #203: the card shows even with nothing detected — a
                    // household whose destination never syncs has no other way
                    // to reach the declare action.
                    if let contributions = context.savingsContributions,
                        !contributions.isEmpty || viewModel.isCurrentMonth {
                        savingsContributionsCard(contributions, viewModel)
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
            .sheet(isPresented: $declaringSavings) {
                if let accounts = model.accounts {
                    DeclareSavingsContributionSheet(
                        viewModel: viewModel, accountsAPI: accounts, currency: context.currency)
                }
            }
        } else {
            ProgressView()
        }
    }

    /// M120 (ADR 0074): the app and box speak different CONTRACTS, so one of
    /// them is genuinely stale - point at the OTA page. A build-only
    /// difference never reaches here.
    private func versionMismatchBanner(server: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label(
                "App v\(OverviewViewModel.appVersion) · box v\(server)",
                systemImage: "exclamationmark.arrow.triangle.2.circlepath"
            )
            .font(.subheadline.weight(.semibold))
            Text(
                String(
                    localized: """
                        App and box are on different versions — some screens may \
                        not match the server. Install the update from your box's \
                        OTA page.
                        """)
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
        NavigationLink {
            CashOutlookDetailView(outlook: outlook)
        } label: {
            Card("Cash outlook", systemImage: "calendar.badge.clock") {
                // ADR 0069: THE headline — will cash cover what's due, and by
                // when must an RSU sale start (4 business days' notice) to
                // close the gap. Front and center per user request 2026-07-26.
                if let sellBy = outlook.sellByDate, let shortDay = outlook.firstShortfallDate {
                    Label(
                        Self.runwayHeadline(outlook) + BillsView.shortDate(sellBy),
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.red)
                    Text(
                        String(localized: "cash runs short \(BillsView.shortDate(shortDay))")
                            + (outlook.shortfall.map {
                                String(localized: " — raise at least \($0.value1.formatted)")
                            } ?? "")
                    )
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.orange)
                } else if outlook.lowestBalance.amountMinor < 0 {
                    Label(
                        "Your cash runs short over the next \(outlook.horizonDays) days",
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.orange)
                } else {
                    Label(
                        "Cash covers everything due in the next \(outlook.horizonDays) days",
                        systemImage: "checkmark.circle.fill"
                    )
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.green)
                }
                Text(verbatim: outlook.lowestBalance.formatted)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .foregroundStyle(outlook.lowestBalance.amountMinor >= 0 ? Color.primary : .red)
                Text(
                    outlook.lowestDate.map {
                        String(
                            localized:
                                "lowest your cash reaches in the next \(outlook.horizonDays) days")
                            + " · \(BillsView.shortDate($0))"
                    }
                        ?? String(
                            localized:
                                "no payments or paydays expected in the next \(outlook.horizonDays) days"
                        )
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(
                    String(
                        localized: """
                            \(outlook.startingCash.formatted) cash + \
                            \(outlook.expectedIncome.formatted) expected paychecks − \
                            \(outlook.obligations.formatted) payments \
                            = \(outlook.endingCash.formatted)
                            """)
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

    /// The runway verb, with the shortfall translated into shares when the
    /// outlook knows them (M-rsu-grants): "Sell RSUs (≈ 12 XYZ) by …".
    static func runwayHeadline(_ outlook: Components.Schemas.CashOutlookResponse) -> String {
        if outlook.runwayAction == .moveCash { return String(localized: "Free up cash by ") }
        if let units = outlook.sellUnits, let ticker = outlook.sellTicker {
            return String(localized: "Sell RSUs (≈ \(units) \(ticker)) by ")
        }
        return String(localized: "Sell RSUs by ")
    }

    /// M113 (ADR 0027): left to spend this month — expected income minus what's
    /// already spent and what's still committed. The accrual counterpart to the
    /// cash outlook's cash-timing view.
    private func spendingPlanCard(_ plan: Components.Schemas.SpendingPlanResponse) -> some View {
        Card("Left to spend this month", systemImage: "chart.pie") {
            Text(verbatim: plan.leftToSpend.formatted)
                .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                .foregroundStyle(plan.leftToSpend.amountMinor >= 0 ? Color.primary : .red)
            if plan.leftToSpend.amountMinor >= 0 {
                Text(
                    String(
                        localized: """
                            about \(plan.perDay.formatted)/day for the remaining \
                            \(plan.daysRemaining) days
                            """)
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            } else {
                Text(
                    String(
                        localized: """
                            this month's spending has outrun this month's income — \
                            the gap is drawing on cash you already had
                            """)
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
        parts.append(
            String(localized: "\(income) expected income (\(received) received + \(toCome) to come)")
        )
        parts.append(String(localized: "\(plan.spent.formatted) spent"))
        parts.append(String(localized: "\(plan.billsRemaining.formatted) bills still due"))
        parts.append(
            String(localized: "\(plan.accountObligations.formatted) loan & lease payments"))
        if plan.plannedSavings.amountMinor > 0 {
            parts.append(String(localized: "\(plan.plannedSavings.formatted) planned savings"))
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
                Text(verbatim: sts.safeToSpend.formatted)
                    .font(.system(.largeTitle, design: .rounded).weight(.semibold))
                    .foregroundStyle(sts.safeToSpend.amountMinor >= 0 ? Color.primary : .red)
                Text(
                    String(
                        localized: """
                            If every commitment were called today — full card balances, all \
                            bills, the emergency fund held back — with no paycheck counted. \
                            Deliberately worst-case; the cash outlook above counts income.
                            """)
                )
                .font(.caption)
                .foregroundStyle(.secondary)
                Text(
                    String(
                        localized: """
                            \(sts.liquidBalance.formatted) liquid − \
                            \(sts.emergencyFundReserved.formatted) emergency fund − \
                            \(sts.billsDue.formatted) bills − \
                            \(sts.minimumDebtPayments.formatted) min. debt
                            """)
                        + ((sts.creditCardPayments?.value1).map {
                            String(localized: " − \($0.formatted) cards")
                        } ?? "")
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
                // Informational companion — never added to the stress-test number.
                if let ready = sts.readyToSell?.value1 {
                    Divider()
                    Label {
                        Text(
                            String(
                                localized: """
                                    Ready to sell: \(ready.value.formatted) in vested RSUs — \
                                    about \(ready.saleNoticeBusinessDays) business days \
                                    to become cash
                                    """)
                        )
                    } icon: {
                        Image(systemName: "chart.line.uptrend.xyaxis")
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
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
            Text(verbatim: context.netWorth.formatted)
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
                Text(verbatim: fund.reserved.formatted)
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
                    String(
                        localized: """
                            \(months.formatted(.number.precision(.fractionLength(1)))) of \
                            \(fund.targetMonthsRecommended.formatted(.number.precision(.fractionLength(0)))) months' expenses
                            """)
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
            // The Year chart's own trio (user report 2026-07-25: "$208 Bills"
            // from the recurring model made no sense against real spending).
            HStack {
                stat("Income", flow.income.formatted, tint: .green)
                Divider()
                stat("Spent", flow.spending.formatted, tint: .orange)
                Divider()
                stat(
                    "Kept", flow.net.formatted,
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
                Text(verbatim: "\(percent)%")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(percent >= 0 ? Color.green : .red)
            } else {
                Text(verbatim: "—").font(.title2.weight(.semibold))
            }
            Text(
                String(
                    localized: """
                        \(rate.monthlyIncome.formatted) income vs \
                        \(rate.averageMonthlySpending.formatted) average spending
                        """)
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            // #6: the observed saving, split across the three sources it came
            // from — transfers you made, payroll deductions, and what simply
            // stayed unspent — so the headline isn't a single opaque number.
            if let breakdown = Self.savingsBreakdown(rate) {
                Text(breakdown)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            // #6 honesty: with no 401(k)/HSA figure declared, payroll saving is
            // invisible and the rate understates — say so, and point at the fix.
            if let note = Self.payrollNote(rate) {
                Label(note, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }

    /// #6: the three observed saving sources, largest concept first — declared
    /// transfers, pre-tax payroll deductions, and the residual that just went
    /// unspent — as "$500 transfers · $2,500 payroll · $300 residual". A source
    /// the server didn't send (older box, feature off) is simply omitted; nil
    /// when none are present, so the caller can skip the line entirely.
    static func savingsBreakdown(_ rate: Components.Schemas.SavingsRate) -> String? {
        var parts: [String] = []
        if let transfers = rate.transfers {
            parts.append(String(localized: "\(transfers.formatted) transfers"))
        }
        if let payroll = rate.payrollDeductions {
            parts.append(String(localized: "\(payroll.formatted) payroll"))
        }
        if let residual = rate.residual {
            parts.append(String(localized: "\(residual.formatted) residual"))
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// #6: the understatement caveat, shown only when the box tells us no
    /// payroll profile is on file (`payroll_profile_present == false`). A nil or
    /// true value means the payroll figure is either counted or unknowable, so
    /// no note. Points at the Income screen, where the earner form takes it.
    static func payrollNote(_ rate: Components.Schemas.SavingsRate) -> String? {
        guard rate.payrollProfilePresent == false else { return nil }
        return String(
            localized: """
                401(k)/HSA payroll saving isn't counted yet, so this rate may \
                understate. Add each earner's annual amounts on the Income screen.
                """)
    }

    /// #201: recurring transfers into savings vehicles, largest first. The
    /// footnote is a correctness requirement, not decoration — payroll
    /// deductions never reach the bank feed, so this is never the whole story.
    /// #203 adds the household's own declarations, which need no detection.
    @ViewBuilder
    private func savingsContributionsCard(
        _ contributions: [Components.Schemas.SavingsContribution],
        _ viewModel: OverviewViewModel
    ) -> some View {
        Card("What you're saving", systemImage: "arrow.down.to.line") {
            if contributions.isEmpty {
                Text(
                    String(
                        localized: """
                            Nothing found in your transfers. If you're paying into something \
                            the app can't see — a 529, a workplace plan — tell it here and \
                            every figure will count it.
                            """)
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            ForEach(Array(contributions.enumerated()), id: \.offset) { index, contribution in
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(verbatim: contribution.destinationName).font(.subheadline)
                            // #207: informational, never a warning — an unsynced 529
                            // is the normal case, not a lower-quality result.
                            if contribution.inferred == true {
                                contributionBadge("inferred")
                            }
                            if contribution.declared == true {
                                contributionBadge("declared")
                            }
                        }
                        Text(Self.contributionDetail(contribution))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        // #4: the goal this money fills — or, when the
                        // destination type points at exactly one goal, the
                        // one-tap offer to link them.
                        if let goalID = contribution.goalId {
                            let goalName =
                                viewModel.goalNames[goalID] ?? String(localized: "a goal")
                            Text("funds \(goalName)")
                                .font(.caption)
                                .foregroundStyle(.tint)
                        } else if canManageSavings,
                            let id = contribution.contributionId,
                            let suggested = contribution.suggestedGoalId,
                            let name = viewModel.goalNames[suggested] {
                            Button {
                                Task {
                                    await viewModel.linkContribution(
                                        contributionID: id, goalID: suggested)
                                }
                            } label: {
                                Text("Fund \(name)?")
                                    .font(.caption.weight(.medium))
                            }
                            .buttonStyle(.plain)
                            .foregroundStyle(.tint)
                        }
                    }
                    Spacer(minLength: 0)
                    if canManageSavings {
                        contributionActions(contribution, viewModel)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if index != contributions.count - 1 {
                    Divider()
                }
            }
            if let total = Self.monthlyTotal(contributions) {
                Divider()
                Text("About \(total.formatted) a month")
                    .font(.subheadline.weight(.semibold))
            }
            if !contributions.isEmpty {
                Text(Self.savingsFootnote(contributions))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if canManageSavings, model.accounts != nil {
                declareButton(prominent: contributions.isEmpty)
            }
        }
    }

    /// With nothing detected this is the whole point of the card, so it leads;
    /// alongside detected rows it's a quiet addition to them.
    @ViewBuilder
    private func declareButton(prominent: Bool) -> some View {
        let button = Button {
            declaringSavings = true
        } label: {
            Label("Declare a contribution", systemImage: "plus.circle")
                .font(.subheadline.weight(.medium))
        }
        if prominent {
            button.buttonStyle(.borderedProminent)
        } else {
            button.buttonStyle(.plain).foregroundStyle(.tint).padding(.top, 2)
        }
    }

    private func contributionBadge(_ text: LocalizedStringKey) -> some View {
        Text(text)
            .font(.caption2)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Color.secondary.opacity(0.15), in: Capsule())
            .foregroundStyle(.secondary)
    }

    /// The card is a stack inside a ScrollView, not a List, so there is no swipe
    /// to attach these to — a menu is the surrounding code's other idiom.
    @ViewBuilder
    private func contributionActions(
        _ contribution: Components.Schemas.SavingsContribution,
        _ viewModel: OverviewViewModel
    ) -> some View {
        if contribution.declared == true, let id = contribution.contributionId {
            Menu {
                // #4: linked rows can be unlinked; the row keeps counting, it
                // just stops filling that goal's funding line.
                if contribution.goalId != nil {
                    Button {
                        Task {
                            await viewModel.linkContribution(contributionID: id, goalID: nil)
                        }
                    } label: {
                        Label("Unlink goal", systemImage: "minus.circle")
                    }
                }
                Button(role: .destructive) {
                    Task { await viewModel.stopTracking(contributionID: id) }
                } label: {
                    Label("Stop tracking", systemImage: "trash")
                }
            } label: {
                contributionActionsLabel(contribution.destinationName)
            }
        } else if let route = Self.dismissableRoute(contribution) {
            Menu {
                Button(role: .destructive) {
                    Task {
                        await viewModel.dismissRoute(
                            sourceAccountID: route.source,
                            destinationAccountID: route.destination)
                    }
                } label: {
                    Label("Not saving", systemImage: "xmark.circle")
                }
            } label: {
                contributionActionsLabel(contribution.destinationName)
            }
        }
    }

    private func contributionActionsLabel(_ name: String) -> some View {
        Image(systemName: "ellipsis.circle")
            .font(.subheadline)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Actions for \(name)")
    }

    /// #203: a detected route can only be dismissed when BOTH of its accounts
    /// are known — the server suppresses the pair, and half a pair names no
    /// route. The source is empty when only the arrival was ever synced.
    static func dismissableRoute(
        _ contribution: Components.Schemas.SavingsContribution
    ) -> (source: String, destination: String)? {
        guard contribution.declared != true,
            let source = contribution.sourceAccountId, !source.isEmpty,
            let destination = contribution.destinationAccountId, !destination.isEmpty
        else { return nil }
        return (source, destination)
    }

    /// The payroll caveat always; the inferred and declared caveats only when a
    /// row carries them.
    static func savingsFootnote(
        _ contributions: [Components.Schemas.SavingsContribution]
    ) -> String {
        var footnote = String(
            localized: """
                Detected from transfers between your accounts. \
                Payroll deductions like a 401(k) don't appear here.
                """)
        if contributions.contains(where: { $0.inferred == true }) {
            footnote += " "
            footnote += String(
                localized: """
                    Rows marked inferred were matched from the money leaving your account — \
                    the destination isn't synced.
                    """)
        }
        if contributions.contains(where: { $0.declared == true }) {
            footnote += " "
            footnote += String(
                localized: """
                    Rows marked declared are your family's own word, counted whether or not \
                    either account syncs.
                    """)
        }
        return footnote
    }

    static func contributionDetail(
        _ contribution: Components.Schemas.SavingsContribution
    ) -> String {
        let cadence = "\(contribution.amount.formattedExact) "
            + "\(cadenceWord(contribution.frequency))"
        // A declared row was never seen in the ledger — that's the whole point
        // of declaring it — so an occurrence count would read "seen 0 times".
        guard contribution.declared != true else {
            return cadence + String(localized: " · declared by your family")
        }
        let times = contribution.occurrences == 1
            ? String(localized: "seen 1 time")
            : String(localized: "seen \(contribution.occurrences) times")
        return cadence + " · \(times)"
    }

    /// Enums are for machines; a row reads "$500 monthly".
    static func cadenceWord(_ frequency: Components.Schemas.RecurringFrequency) -> String {
        switch frequency {
        case .weekly: return String(localized: "weekly")
        case .biweekly: return String(localized: "every two weeks")
        case .semimonthly: return String(localized: "twice a month")
        case .monthly: return String(localized: "monthly")
        case .quarterly: return String(localized: "quarterly")
        case .semiannual: return String(localized: "twice a year")
        case .annual: return String(localized: "yearly")
        }
    }

    /// Cadences differ, so only the server's monthly_equivalent can be summed —
    /// adding the raw amounts would call a yearly $6,000 a monthly $6,000.
    static func monthlyTotal(
        _ contributions: [Components.Schemas.SavingsContribution]
    ) -> Components.Schemas.Money? {
        guard let first = contributions.first else { return nil }
        return .init(
            amountMinor: contributions.reduce(0) { $0 + $1.monthlyEquivalent.amountMinor },
            currency: first.monthlyEquivalent.currency)
    }

    /// M118: the summary card now opens the full envelope manager (parity with
    /// the dashboard's Budgets page).
    @ViewBuilder
    private func budgetCard(_ budgets: Components.Schemas.BudgetSummary) -> some View {
        let card = Card("Budgets", systemImage: "chart.pie") {
            HStack {
                stat("Budgets", "\(budgets.envelopeCount)", tint: .secondary)
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
                Text("Tap to manage budgets")
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
            Text(verbatim: goal.name).font(.headline)
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
                        Text(verbatim: bill.name).font(.subheadline)
                        Text(Self.dueDescription(daysUntil: bill.daysUntil))
                            .font(.caption)
                            .foregroundStyle(bill.daysUntil <= 3 ? Color.orange : .secondary)
                    }
                    Spacer()
                    Text(verbatim: bill.amount.formattedExact)
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
        case ..<0: return String(localized: "Overdue")
        case 0: return String(localized: "Due today")
        case 1: return String(localized: "Due tomorrow")
        default: return String(localized: "Due in \(daysUntil) days")
        }
    }

    private func stat(_ label: LocalizedStringKey, _ value: String, tint: Color) -> some View {
        VStack(spacing: 2) {
            Text(verbatim: value)
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
    let title: LocalizedStringKey
    let systemImage: String
    @ViewBuilder let content: Content

    init(_ title: LocalizedStringKey, systemImage: String, @ViewBuilder content: () -> Content) {
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
