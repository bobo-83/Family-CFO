import SwiftUI
import WidgetKit

/// Watch-face complications (ADR 0067 v5): the Glance page's lead number in a
/// face slot. Reads ONLY the snapshot the watch app cached to the App Group —
/// no network from the widget — and marks amounts privacy-sensitive so the
/// system can redact them on a locked or shared face.

struct GlanceEntry: TimelineEntry {
    let date: Date
    let snapshot: WatchFaceSnapshot?
}

struct GlanceProvider: TimelineProvider {
    private let store = WatchFaceSnapshotStore()

    func placeholder(in context: Context) -> GlanceEntry {
        GlanceEntry(
            date: Date(),
            snapshot: WatchFaceSnapshot(
                leftToSpendMinor: 1_272_800, safeToSpendMinor: 1_050_700,
                lowestBalanceMinor: 6_641_700, netWorthMinor: nil,
                monthIncomeMinor: 3_029_800, monthSpendingMinor: 1_260_400,
                expectedIncomeMinor: 3_029_800,
                currency: "USD", capturedAt: Date(),
                budgets: [
                    .init(name: "Groceries", limitMinor: 120_000, spentMinor: 104_000),
                    .init(name: "Dining", limitMinor: 60_000, spentMinor: 41_000),
                    .init(name: "Fun", limitMinor: 40_000, spentMinor: 12_000),
                ]))
    }

    func getSnapshot(in context: Context, completion: @escaping (GlanceEntry) -> Void) {
        completion(GlanceEntry(date: Date(), snapshot: store.load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<GlanceEntry>) -> Void) {
        // One entry: the app reloads the timeline whenever it writes fresh
        // values; a daily refresh keeps the relative age from going absurd.
        let entry = GlanceEntry(date: Date(), snapshot: store.load())
        let next = Calendar.current.date(byAdding: .day, value: 1, to: Date()) ?? Date()
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

struct GlanceComplicationView: View {
    @Environment(\.widgetFamily) private var family
    let entry: GlanceEntry

    var body: some View {
        Group {
            if let snapshot = entry.snapshot, let headline = snapshot.headline {
                slot(snapshot, headline)
            } else {
                empty
            }
        }
        .containerBackground(for: .widget) { Color.clear }
    }

    @ViewBuilder
    private func slot(_ snapshot: WatchFaceSnapshot, _ headline: (label: String, amountMinor: Int64)) -> some View {
        switch family {
        case .accessoryRectangular:
            monthDetail(snapshot, headline)
        case .accessoryInline:
            Text("\(snapshot.compact(headline.amountMinor)) left")
                .privacySensitive()
        case .accessoryCorner:
            // The cash pile sits in the corner; the amount rides the arc.
            if let signal = snapshot.cashSignal {
                CashStackView(signal: signal)
                    .widgetLabel {
                        Text("\(snapshot.compact(headline.amountMinor)) left")
                            .privacySensitive()
                    }
            } else {
                Text(snapshot.compact(headline.amountMinor))
                    .font(.system(.body, design: .rounded).weight(.semibold))
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                    .privacySensitive()
                    .widgetLabel(headline.label)
            }
        default:  // circular: the cash meter (ADR 0067 v8) — 1..5 bills, torn = red
            if let signal = snapshot.cashSignal {
                CashStackView(signal: signal)
                    .widgetLabel {
                        Text("\(snapshot.compact(headline.amountMinor)) left")
                            .privacySensitive()
                    }
            } else {
                Text(snapshot.compact(headline.amountMinor))
                    .font(.system(.body, design: .rounded).weight(.semibold))
                    .minimumScaleFactor(0.5)
                    .lineLimit(1)
                    .privacySensitive()
                    .widgetLabel(headline.label)
            }
        }
    }

    /// The month at a glance for the big slot: In and Out bars scaled against
    /// each other, then the number the family acts on.
    @ViewBuilder
    private func monthDetail(_ snapshot: WatchFaceSnapshot, _ headline: (label: String, amountMinor: Int64)) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            if let income = snapshot.monthIncomeMinor, let spending = snapshot.monthSpendingMinor {
                let peak = max(income, spending, 1)
                barRow("In", income, peak, .green, snapshot)
                barRow("Out", spending, peak, .orange, snapshot)
            }
            HStack {
                Text(headline.label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(snapshot.compact(headline.amountMinor))
                    .font(.caption.weight(.semibold))
                    .privacySensitive()
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func barRow(
        _ label: String, _ minor: Int64, _ peak: Int64, _ tint: Color,
        _ snapshot: WatchFaceSnapshot
    ) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .frame(width: 24, alignment: .leading)
            ProgressView(value: Double(minor) / Double(peak))
                .tint(tint)
            Text(snapshot.compact(minor))
                .font(.caption2)
                .privacySensitive()
        }
    }

    private var empty: some View {
        // No snapshot yet: the app hasn't loaded a Glance since install.
        Image(systemName: "dollarsign.circle")
            .widgetLabel("Open Family CFO")
    }
}

struct FamilyCFOWatchGlanceWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: WatchFaceSnapshot.widgetKind, provider: GlanceProvider()) { entry in
            GlanceComplicationView(entry: entry)
        }
        .configurationDisplayName("Money glance")
        .description("Left to spend (or safe to spend) from your own box, at a glance.")
        .supportedFamilies([
            .accessoryCircular, .accessoryCorner, .accessoryInline, .accessoryRectangular,
        ])
    }
}

@main
struct FamilyCFOWatchWidgetsBundle: WidgetBundle {
    var body: some Widget {
        FamilyCFOWatchGlanceWidget()
        FamilyCFOWatchBudgetsWidget()
    }
}
