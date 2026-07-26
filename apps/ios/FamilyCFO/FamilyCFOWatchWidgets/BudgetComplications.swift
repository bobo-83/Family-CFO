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

    /// EVERY budget as a vertical column (user request 2026-07-25 — three
    /// horizontal rows hid the rest): fill height = usage, tinted like the
    /// rows were, most at-risk first (the snapshot pre-sorts). The overall
    /// percent anchors the right edge.
    private func budgetChart(_ snapshot: WatchFaceSnapshot) -> some View {
        HStack(alignment: .bottom, spacing: 3) {
            ForEach(snapshot.budgets ?? [], id: \.name) { slice in
                VStack(spacing: 1) {
                    GeometryReader { geometry in
                        ZStack(alignment: .bottom) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color.gray.opacity(0.25))
                            RoundedRectangle(cornerRadius: 2)
                                .fill(tint(slice.fraction))
                                .frame(
                                    height: max(
                                        3, geometry.size.height * min(slice.fraction, 1)))
                        }
                    }
                    // The category's own icon (same mapping as the phone's
                    // picker) says which budget each column is.
                    Image(systemName: CategoryIcon.symbol(for: slice.name))
                        .font(.system(size: 12))
                        .minimumScaleFactor(0.7)
                        .frame(height: 13)
                        .foregroundStyle(.secondary)
                }
            }
            // With many columns the percent has no room and wraps into
            // noise (real face, 2026-07-25) — the ring slot carries it anyway.
            if (snapshot.budgets?.count ?? 0) <= 6, let fraction = snapshot.budgetFraction {
                Text(percent(fraction))
                    .font(.caption2)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .privacySensitive()
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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
