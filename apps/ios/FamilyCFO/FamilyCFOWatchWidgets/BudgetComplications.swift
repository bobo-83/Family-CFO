import SwiftUI
import WidgetKit

/// Budget complications (ADR 0067 v9): the small slots ring how much of the
/// whole monthly budget is burned; the rectangular slot charts the budgets
/// closest to their caps. Same contract as the glance widget — cached
/// snapshot only, no network, amounts privacy-sensitive.

struct BudgetComplicationView: View {
    @Environment(\.widgetFamily) private var family
    let entry: GlanceEntry

    var body: some View {
        Group {
            if let snapshot = entry.snapshot, let fraction = snapshot.budgetFraction {
                slot(snapshot, fraction)
            } else {
                Image(systemName: "chart.pie")
                    .widgetLabel("Open Family CFO")
            }
        }
        .containerBackground(for: .widget) { Color.clear }
    }

    private func tint(_ fraction: Double) -> Color {
        if fraction >= 1 { return .red }
        if fraction >= 0.8 { return .orange }
        return .green
    }

    private func percent(_ fraction: Double) -> String {
        "\(Int((fraction * 100).rounded()))%"
    }

    @ViewBuilder
    private func slot(_ snapshot: WatchFaceSnapshot, _ fraction: Double) -> some View {
        switch family {
        case .accessoryRectangular:
            budgetChart(snapshot)
        case .accessoryInline:
            Text("Budget \(percent(fraction)) used")
                .privacySensitive()
        case .accessoryCorner:
            Text(percent(fraction))
                .font(.system(.body, design: .rounded).weight(.semibold))
                .minimumScaleFactor(0.5)
                .privacySensitive()
                .widgetLabel {
                    Gauge(value: min(fraction, 1)) { Text("Budget") }
                        .tint(tint(fraction))
                }
        default:  // circular: the budget ring
            Gauge(value: min(fraction, 1)) {
                Text("Bdgt")
            } currentValueLabel: {
                Text(percent(fraction))
                    .minimumScaleFactor(0.5)
                    .privacySensitive()
            }
            .gaugeStyle(.accessoryCircular)
            .tint(tint(fraction))
            .widgetLabel {
                Text("Budget \(percent(fraction)) used")
                    .privacySensitive()
            }
        }
    }

    /// The three budgets closest to their caps (the snapshot pre-sorts by
    /// usage), each a name + burn bar + percent.
    private func budgetChart(_ snapshot: WatchFaceSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach((snapshot.budgets ?? []).prefix(3), id: \.name) { slice in
                HStack(spacing: 4) {
                    Text(slice.name)
                        .font(.caption2)
                        .lineLimit(1)
                        .frame(width: 52, alignment: .leading)
                    ProgressView(value: min(slice.fraction, 1))
                        .tint(tint(slice.fraction))
                    Text(percent(slice.fraction))
                        .font(.caption2)
                        .monospacedDigit()
                        .privacySensitive()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct FamilyCFOWatchBudgetsWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(
            kind: WatchFaceSnapshot.budgetsWidgetKind, provider: GlanceProvider()
        ) { entry in
            BudgetComplicationView(entry: entry)
        }
        .configurationDisplayName("Budgets")
        .description("How much of this month's budgets is used, from your own box.")
        .supportedFamilies([
            .accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular,
        ])
    }
}
