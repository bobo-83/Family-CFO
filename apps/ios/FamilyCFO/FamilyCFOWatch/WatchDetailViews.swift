import Charts
import SwiftUI

/// Drill-downs behind the glance rows (user request 2026-07-25): the same
/// component numbers the phone's detail sheets show, wrist-sized, plus the
/// net-worth trend as a real chart.
struct WatchSafeToSpendDetail: View {
    let safeToSpend: Components.Schemas.SafeToSpend

    var body: some View {
        List {
            row("Liquid cash", safeToSpend.liquidBalance)
            row("Emergency fund held back", safeToSpend.emergencyFundReserved, negative: true)
            row("Bills due", safeToSpend.billsDue, negative: true)
            row("Debt minimums", safeToSpend.minimumDebtPayments, negative: true)
            if let amount = safeToSpend.safeToSpend {
                HStack {
                    Text("Safe to spend").font(.footnote.weight(.semibold))
                    Spacer()
                    Text(amount.formatted)
                        .foregroundStyle(amount.amountMinor >= 0 ? .green : .red)
                }
                if let note = amount.partialDisclosure {
                    Text(note).font(.caption2).foregroundStyle(.secondary)
                }
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    row("Safe to spend", "Unavailable")
                    if let note = WatchSafeToSpendBlockers.copy(
                        subscriptionIncompleteCount:
                            safeToSpend.subscriptionDetection.isUnavailable
                            ? safeToSpend.subscriptionDetection.incompleteCount : nil,
                        savingsIncompleteCount:
                            safeToSpend.savingsDetection.isUnavailable
                            ? safeToSpend.savingsDetection.incompleteCount : nil)
                    {
                        Text(note).font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
        // Clear of the page-indicator dots (user report 2026-07-25).
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle("Safe to spend")
    }

    private func row(_ label: String, _ money: Components.Schemas.Money, negative: Bool = false) -> some View {
        row(label, (negative ? "−" : "") + money.formatted, color: negative ? .orange : .primary)
    }

    private func row(
        _ label: String,
        _ money: Components.Schemas.QualifiedMoney,
        negative: Bool = false
    ) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            row(label, (negative ? "−" : "") + money.formatted, color: negative ? .orange : .primary)
            if let note = money.partialDisclosure {
                Text(note).font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private func row(_ label: String, _ value: String, color: Color = .secondary) -> some View {
        HStack {
            Text(label).font(.footnote)
            Spacer()
            Text(value).font(.footnote).foregroundStyle(color)
        }
    }
}

struct WatchNetWorthDetail: View {
    let context: Components.Schemas.HouseholdContext

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                Text(context.netWorth.formatted)
                    .font(.title3.weight(.semibold))
                    .accessibilityLabel(context.netWorth.accessibilityDescription)
                if let note = context.netWorth.partialDisclosure {
                    Text(note).font(.caption2).foregroundStyle(.secondary)
                }
                if let history = context.netWorthHistory, history.count > 1 {
                    Chart(history, id: \.asOf) { point in
                        LineMark(
                            x: .value("Date", point.asOf),
                            y: .value("Net worth", point.netWorth.decimalValue)
                        )
                        .interpolationMethod(.catmullRom)
                    }
                    .chartXAxis(.hidden)
                    .frame(height: 80)
                    Text("\(history.count) snapshots")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                } else {
                    Text("The trend appears after a few daily snapshots.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle("Net worth")
    }
}

/// The year at a glance on the wrist: the same monthly in/out trend the
/// phone's Year mode charts, from `GET /overview/yearly`.
struct WatchYearTrendView: View {
    @Environment(WatchModel.self) private var model
    @State private var overview: Components.Schemas.YearlyOverview?
    @State private var errorMessage: String?
    /// ADR 0068 (amended): tapping a bar shows that month's numbers here too.
    @State private var selectedMonth: String?
    @State private var generation: UInt64 = 0
    @State private var currentOwner: LoadOwner?
    @State private var committedOwner: LoadOwner?

    private struct LoadOwner: Equatable {
        let identity: String
        let revision: UInt64
        let generation: UInt64
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if let overview, belongsToCurrentSession(committedOwner) {
                    if overview.months.isEmpty {
                        Text("No data for \(String(overview.year)) yet.")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else {
                        trendChart(overview)
                        monthDetail(overview)
                        yearTotals(overview)
                    }
                } else if let errorMessage, belongsToCurrentSession(currentOwner) {
                    Text(errorMessage).font(.caption2).foregroundStyle(.red)
                } else {
                    ProgressView()
                }
            }
        }
        .contentMargins(.trailing, 10, for: .scrollContent)
        .navigationTitle("This year")
        .task(id: model.sessionRevision) {
            await load(revision: model.sessionRevision, resetForSession: true)
        }
        .refreshable { await load(revision: model.sessionRevision) }
    }

    // Canonical series form (matches the iOS Year chart): foregroundStyle(by:)
    // + an explicit scale. Pairing position(by:) with FIXED styles rendered
    // fine in the simulator but crashed on the watch as the page scrolled in
    // (user report 2026-07-25).
    private func trendChart(_ overview: Components.Schemas.YearlyOverview) -> some View {
        Chart {
            ForEach(overview.months, id: \.month) { month in
                BarMark(
                    x: .value("Month", String(month.month.suffix(2))),
                    y: .value("Amount", month.income.decimalValue)
                )
                .foregroundStyle(by: .value("Series", "In"))
                .position(by: .value("Series", "In"))
                .opacity(barOpacity(month.month))
                BarMark(
                    x: .value("Month", String(month.month.suffix(2))),
                    y: .value("Amount", month.spending.decimalValue)
                )
                .foregroundStyle(by: .value("Series", "Out"))
                .position(by: .value("Series", "Out"))
                .opacity(barOpacity(month.month))
            }
        }
        .chartForegroundStyleScale(["In": Color.green, "Out": Color.orange])
        .chartLegend(.hidden)
        .chartYAxis(.hidden)
        .frame(height: 90)
        // Tap a bar for that month's numbers, like the phone (user report
        // 2026-07-25). Tap again to clear.
        .chartOverlay { proxy in
            GeometryReader { geometry in
                Rectangle().fill(.clear).contentShape(Rectangle())
                    .onTapGesture { location in
                        let origin = geometry[proxy.plotFrame!].origin
                        if let label: String = proxy.value(atX: location.x - origin.x),
                            let month = overview.months.first(where: {
                                $0.month.hasSuffix(label)
                            })
                        {
                            selectedMonth =
                                selectedMonth == month.month ? nil : month.month
                        }
                    }
            }
        }
    }

    private func barOpacity(_ month: String) -> Double {
        selectedMonth == nil || selectedMonth == month ? 1 : 0.35
    }

    @ViewBuilder
    private func monthDetail(_ overview: Components.Schemas.YearlyOverview) -> some View {
        if let month = overview.months.first(where: { $0.month == selectedMonth }) {
            Text(YearChartText.longLabel(month.month))
                .font(.caption.weight(.semibold))
            HStack {
                label("In", month.income.formatted, .green)
                label("Out", month.spending.formatted, .orange)
            }
            if let net = month.net {
                label("Kept", net.formatted, net.amountMinor >= 0 ? .green : .red)
            } else {
                label("Kept", "Unavailable", .secondary)
            }
            qualificationNotes(month.income, month.spending)
            Divider()
            Text("All of \(String(overview.year))")
                .font(.caption2)
                .foregroundStyle(.secondary)
        } else {
            Text("Tap a month for its numbers.")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder
    private func yearTotals(_ overview: Components.Schemas.YearlyOverview) -> some View {
        HStack {
            label("In", overview.totalIncome.formatted, .green)
            label("Out", overview.totalSpending.formatted, .orange)
        }
        if let net = overview.totalNet {
            label("Kept", net.formatted, net.amountMinor >= 0 ? .green : .red)
        } else {
            label("Kept", "Unavailable", .secondary)
        }
        qualificationNotes(overview.totalIncome, overview.totalSpending)
        if let review = overview.review {
            Text(review.summary)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private func qualificationNotes(
        _ first: Components.Schemas.QualifiedMoney,
        _ second: Components.Schemas.QualifiedMoney
    ) -> some View {
        if let note = first.partialDisclosure {
            Text(note).font(.caption2).foregroundStyle(.secondary)
        }
        if let note = second.partialDisclosure, second.incompleteCount != first.incompleteCount {
            Text(note).font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func label(_ name: String, _ value: String, _ color: Color) -> some View {
        HStack {
            Text(name).font(.caption2).foregroundStyle(.secondary)
            Spacer()
            Text(value).font(.caption).foregroundStyle(color)
        }
    }

    private func load(revision: UInt64, resetForSession: Bool = false) async {
        generation &+= 1
        if resetForSession { resetSettledState() }
        guard revision == model.sessionRevision,
            let identity = model.sessionIdentity, let client = model.client
        else {
            currentOwner = nil
            return
        }
        let owner = LoadOwner(
            identity: identity, revision: revision, generation: generation)
        currentOwner = owner
        do {
            let result = try await client.getYearlyOverview(.init())
            guard owns(owner), !Task.isCancelled else { return }
            guard case .ok(let response) = result else {
                // Stale relayed token (ADR 0067 v6): refresh from the phone, retry once.
                let refreshed = await model.requestFreshCredential(
                    expectedIdentity: owner.identity)
                if refreshed { return }  // the revision-keyed task restarts with the new credential
                guard owns(owner), !Task.isCancelled else { return }
                errorMessage = "The box answered unexpectedly."
                return
            }
            let loaded = try response.body.json
            guard owns(owner), !Task.isCancelled else { return }
            overview = loaded
            committedOwner = owner
            errorMessage = nil
        } catch {
            guard owns(owner), !Task.isCancelled else { return }
            errorMessage = "Can't reach the box."
        }
    }

    private func owns(_ owner: LoadOwner) -> Bool {
        currentOwner == owner
            && model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func belongsToCurrentSession(_ owner: LoadOwner?) -> Bool {
        guard let owner else { return false }
        return model.sessionIdentity == owner.identity
            && model.sessionRevision == owner.revision
    }

    private func resetSettledState() {
        currentOwner = nil
        committedOwner = nil
        overview = nil
        errorMessage = nil
        selectedMonth = nil
    }
}
