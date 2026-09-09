import SwiftUI
import WidgetKit

/// Budget complications use server-owned summary primitives and per-envelope
/// percentages. They never rebuild aggregate budget arithmetic from slices.
struct BudgetComplicationView: View {
    @Environment(\.widgetFamily) private var family
    let entry: GlanceEntry

    var body: some View {
        Group {
            if let snapshot = entry.snapshot, snapshot.budgetSpentMinor != nil {
                slot(snapshot)
            } else {
                Image(systemName: "chart.pie")
                    .widgetLabel("Open Family CFO")
            }
        }
        .containerBackground(for: .widget) { Color.clear }
    }

    private func summaryTint(_ snapshot: WatchFaceSnapshot) -> Color {
        guard let over = snapshot.budgetOverCount,
              let warning = snapshot.budgetWarningCount
        else { return .secondary }
        if over > 0 { return .red }
        if warning > 0 { return .orange }
        return .green
    }

    private func summaryLabel(_ snapshot: WatchFaceSnapshot) -> String {
        if let count = snapshot.budgetSpentIncompleteCount, count > 0 {
            if count == 1 {
                return String(
                    localized: "Partial total—1 stored amount could not be read and was left out.")
            }
            return String(
                localized: "Partial total—\(count) stored amounts could not be read and were left out.")
        }
        return snapshot.budgetStatusLabel ?? String(localized: "Unavailable")
    }

    @ViewBuilder
    private func slot(_ snapshot: WatchFaceSnapshot) -> some View {
        switch family {
        case .accessoryRectangular:
            budgetChart(snapshot)
        case .accessoryInline:
            Text("Budget \(summaryLabel(snapshot))")
                .privacySensitive()
        case .accessoryCorner:
            Text(summaryLabel(snapshot))
                .font(.system(.body, design: .rounded).weight(.semibold))
                .minimumScaleFactor(0.5)
                .lineLimit(1)
                .privacySensitive()
                .widgetLabel(summaryLabel(snapshot))
        default:
            Text(summaryLabel(snapshot))
                .font(.system(.body, design: .rounded).weight(.semibold))
                .minimumScaleFactor(0.5)
                .lineLimit(1)
                .privacySensitive()
                .widgetLabel(summaryLabel(snapshot))
        }
    }

    /// Each column uses the server's nullable per-envelope percentage. Missing
    /// decisions stay neutral and do not become empty/success bars.
    private func budgetChart(_ snapshot: WatchFaceSnapshot) -> some View {
        HStack(alignment: .bottom, spacing: 3) {
            ForEach(snapshot.budgets ?? [], id: \.name) { slice in
                VStack(spacing: 1) {
                    GeometryReader { geometry in
                        ZStack(alignment: .bottom) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color.gray.opacity(0.25))
                            if let fraction = slice.fraction {
                                RoundedRectangle(cornerRadius: 2)
                                    .fill(
                                        fraction >= 1 ? Color.red
                                            : fraction >= 0.8 ? Color.orange : Color.green)
                                    .frame(
                                        height: max(
                                            3, geometry.size.height * min(fraction, 1)))
                            } else {
                                Image(systemName: "questionmark")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                            }
                        }
                    }
                    Image(systemName: CategoryIcon.symbol(for: slice.name))
                        .font(.system(size: 12))
                        .minimumScaleFactor(0.7)
                        .frame(height: 13)
                        .foregroundStyle(.secondary)
                }
            }
            if (snapshot.budgets?.count ?? 0) <= 6 {
                Text(summaryLabel(snapshot))
                    .font(.caption2)
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
        .description("Your server-calculated monthly budget status, from your own box.")
        .supportedFamilies([
            .accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular,
        ])
    }
}
