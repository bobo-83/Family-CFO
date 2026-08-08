import Charts
import SwiftUI

/// The Overview's Year mode (M-yearly): a monthly income-vs-spending trend
/// with a net-worth line, year totals, the grounded review (summary +
/// suggestions, regenerable), and the year's top categories. Tapping a month
/// drills into that month via the Overview's existing month navigation.
struct YearlyOverviewView: View {
    /// #31: the series names are Swift Charts SCALE KEYS as well as legend
    /// text — the marks and `chartForegroundStyleScale` must use the identical
    /// string or the colours detach. Localized once here so both sides agree.
    private static let seriesIncome = String(localized: "Income")
    private static let seriesSpending = String(localized: "Spending")

    @Environment(AppModel.self) private var model
    @State var viewModel: YearlyOverviewViewModel
    /// Drill-down: hand the tapped month ("yyyy-MM") back to the Overview.
    let onSelectMonth: (String) -> Void
    /// ADR 0068: a tap SELECTS a month — its exact numbers show in a card with
    /// explicit actions (explain via advisor, open the month). Never navigates
    /// by itself.
    @State private var selectedMonth: String?

    var body: some View {
        Group {
            yearPicker
            if let overview = viewModel.overview {
                if overview.months.isEmpty {
                    Text("No transactions recorded in \(String(viewModel.year)).")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.vertical, 24)
                } else {
                    trendChart(overview)
                    totalsRow(overview)
                    reviewCard(overview)
                    topCategories(overview)
                }
            } else if viewModel.isLoading {
                ProgressView().frame(maxWidth: .infinity, alignment: .center)
            }
            if let error = viewModel.errorMessage {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .task { if viewModel.overview == nil { await viewModel.load() } }
    }

    private var yearPicker: some View {
        HStack {
            Button {
                Task { await viewModel.step(-1) }
            } label: {
                Image(systemName: "chevron.left")
            }
            Spacer()
            Text(verbatim: String(viewModel.year)).font(.headline)
            Spacer()
            Button {
                Task { await viewModel.step(1) }
            } label: {
                Image(systemName: "chevron.right")
            }
            .disabled(viewModel.year >= Calendar.current.component(.year, from: .now))
        }
        .buttonStyle(.borderless)
    }

    @ViewBuilder private func trendChart(_ overview: Components.Schemas.YearlyOverview) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Each month, in and out").font(.subheadline.weight(.semibold))
            Chart {
                ForEach(overview.months, id: \.month) { month in
                    BarMark(
                        x: .value("Month", Self.shortLabel(month.month)),
                        y: .value(Self.seriesIncome, month.income.decimalValue)
                    )
                    .foregroundStyle(by: .value("Series", Self.seriesIncome))
                    .position(by: .value("Series", Self.seriesIncome))
                    .opacity(barOpacity(month.month))
                    BarMark(
                        x: .value("Month", Self.shortLabel(month.month)),
                        y: .value(Self.seriesSpending, month.spending.decimalValue)
                    )
                    .foregroundStyle(by: .value("Series", Self.seriesSpending))
                    .position(by: .value("Series", Self.seriesSpending))
                    .opacity(barOpacity(month.month))
                }
            }
            .chartForegroundStyleScale([Self.seriesIncome: Color.green, Self.seriesSpending: Color.orange])
            .frame(height: 190)
            // A tap selects the month (tap again to clear); the card below
            // carries the values and the explicit actions (ADR 0068).
            .chartOverlay { proxy in
                GeometryReader { geometry in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .onTapGesture { location in
                            let origin = geometry[proxy.plotFrame!].origin
                            let x = location.x - origin.x
                            if let label: String = proxy.value(atX: x),
                                let month = overview.months.first(where: {
                                    Self.shortLabel($0.month) == label
                                })
                            {
                                withAnimation(.snappy) {
                                    selectedMonth =
                                        selectedMonth == month.month ? nil : month.month
                                }
                            }
                        }
                }
            }
            if let month = overview.months.first(where: { $0.month == selectedMonth }) {
                selectedMonthCard(month)
            } else {
                Text("Tap a month to see its numbers.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .onChange(of: viewModel.year) { _, _ in selectedMonth = nil }
    }

    private func barOpacity(_ month: String) -> Double {
        selectedMonth == nil || selectedMonth == month ? 1 : 0.35
    }

    /// The tapped month's exact figures, plus what to do with them: have the
    /// advisor explain either flow, or open the month's full Overview.
    private func selectedMonthCard(_ month: Components.Schemas.YearMonthSummary) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(verbatim: Self.longLabel(month.month)).font(.subheadline.weight(.semibold))
                Spacer()
                Button("Open month") { onSelectMonth(month.month) }
                    .font(.caption)
                    .buttonStyle(.bordered)
            }
            HStack {
                totalCell("In", month.income.formatted, .green)
                totalCell("Out", month.spending.formatted, .orange)
                totalCell("Kept", month.net.formatted, month.net.amountMinor >= 0 ? .green : .red)
                if let netWorth = month.netWorthEom {
                    totalCell("Net worth", netWorth.formatted, .primary)
                }
            }
            if model.rolePolicy.canChat {
                HStack {
                    Button {
                        model.askAdvisor(Self.incomeQuestion(for: month.month))
                    } label: {
                        Label("Explain income", systemImage: "sparkles").font(.caption)
                    }
                    Button {
                        model.askAdvisor(Self.spendingQuestion(for: month.month))
                    } label: {
                        Label("Explain spending", systemImage: "sparkles").font(.caption)
                    }
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 12))
    }

    /// Shared wording (YearChartText) so phone, watch, and web ask alike.
    static func incomeQuestion(for month: String) -> String {
        YearChartText.incomeQuestion(for: month)
    }

    static func spendingQuestion(for month: String) -> String {
        YearChartText.spendingQuestion(for: month)
    }

    private func totalsRow(_ overview: Components.Schemas.YearlyOverview) -> some View {
        HStack {
            totalCell("In", overview.totalIncome.formatted, .green)
            totalCell("Out", overview.totalSpending.formatted, .orange)
            totalCell("Kept", overview.totalNet.formatted, overview.totalNet.amountMinor >= 0 ? .green : .red)
        }
    }

    private func totalCell(
        _ label: LocalizedStringKey, _ value: String, _ color: Color
    ) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(verbatim: value).font(.callout.weight(.semibold)).foregroundStyle(color)
        }
        .frame(maxWidth: .infinity)
    }

    @ViewBuilder private func reviewCard(_ overview: Components.Schemas.YearlyOverview) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("The year so far").font(.subheadline.weight(.semibold))
                Spacer()
                Button {
                    Task { await viewModel.generateReview() }
                } label: {
                    if viewModel.isGenerating {
                        ProgressView()
                    } else {
                        Label(
                            overview.review == nil
                                ? String(localized: "Write it") : String(localized: "Refresh"),
                            systemImage: "sparkles"
                        )
                        .font(.caption)
                    }
                }
                .buttonStyle(.bordered)
                .disabled(viewModel.isGenerating)
            }
            if let review = overview.review {
                Text(verbatim: review.summary).font(.callout)
                if !review.suggestions.isEmpty {
                    Text("Could be better").font(.caption.weight(.semibold)).padding(.top, 2)
                    ForEach(review.suggestions, id: \.self) { suggestion in
                        Label(suggestion, systemImage: "lightbulb")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                if let model = review.model {
                    Text("🤖 Written by \(model), from this year's real figures.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                } else {
                    Text("🧮 Computed summary — enable the AI runtime for a narrative.")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            } else if !viewModel.isGenerating {
                Text("Ask the advisor to sum up the year and suggest improvements.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private func topCategories(_ overview: Components.Schemas.YearlyOverview) -> some View {
        if !overview.topCategories.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Where it went").font(.subheadline.weight(.semibold))
                ForEach(overview.topCategories, id: \.name) { entry in
                    HStack {
                        Text(verbatim: entry.name).font(.callout)
                        Spacer()
                        Text(verbatim: entry.amount.formatted).font(.callout)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    static func shortLabel(_ month: String) -> String {
        YearChartText.shortLabel(month)
    }

    static func longLabel(_ month: String) -> String {
        YearChartText.longLabel(month)
    }
}
