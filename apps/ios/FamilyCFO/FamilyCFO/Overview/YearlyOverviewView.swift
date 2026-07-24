import Charts
import SwiftUI

/// The Overview's Year mode (M-yearly): a monthly income-vs-spending trend
/// with a net-worth line, year totals, the grounded review (summary +
/// suggestions, regenerable), and the year's top categories. Tapping a month
/// drills into that month via the Overview's existing month navigation.
struct YearlyOverviewView: View {
    @State var viewModel: YearlyOverviewViewModel
    /// Drill-down: hand the tapped month ("yyyy-MM") back to the Overview.
    let onSelectMonth: (String) -> Void

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
            Text(String(viewModel.year)).font(.headline)
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
                        y: .value("Income", month.income.decimalValue)
                    )
                    .foregroundStyle(by: .value("Series", "Income"))
                    .position(by: .value("Series", "Income"))
                    BarMark(
                        x: .value("Month", Self.shortLabel(month.month)),
                        y: .value("Spending", month.spending.decimalValue)
                    )
                    .foregroundStyle(by: .value("Series", "Spending"))
                    .position(by: .value("Series", "Spending"))
                }
            }
            .chartForegroundStyleScale(["Income": Color.green, "Spending": Color.orange])
            .frame(height: 190)
            // Tap a month column to drill into that month's full Overview.
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
                                onSelectMonth(month.month)
                            }
                        }
                }
            }
            Text("Tap a month to open it.").font(.caption2).foregroundStyle(.secondary)
        }
    }

    private func totalsRow(_ overview: Components.Schemas.YearlyOverview) -> some View {
        HStack {
            totalCell("In", overview.totalIncome.formatted, .green)
            totalCell("Out", overview.totalSpending.formatted, .orange)
            totalCell("Kept", overview.totalNet.formatted, overview.totalNet.amountMinor >= 0 ? .green : .red)
        }
    }

    private func totalCell(_ label: String, _ value: String, _ color: Color) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.callout.weight(.semibold)).foregroundStyle(color)
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
                            overview.review == nil ? "Write it" : "Refresh",
                            systemImage: "sparkles"
                        )
                        .font(.caption)
                    }
                }
                .buttonStyle(.bordered)
                .disabled(viewModel.isGenerating)
            }
            if let review = overview.review {
                Text(review.summary).font(.callout)
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
                        Text(entry.name).font(.callout)
                        Spacer()
                        Text(entry.amount.formatted).font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    static func shortLabel(_ month: String) -> String {
        // "2026-03" -> "Mar"
        guard let number = Int(month.suffix(2)), (1...12).contains(number) else { return month }
        return Calendar.current.shortMonthSymbols[number - 1]
    }
}
