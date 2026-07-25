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
                currency: "USD", capturedAt: Date()))
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
            VStack(alignment: .leading, spacing: 1) {
                Text(headline.label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(snapshot.formatted(headline.amountMinor))
                    .font(.headline)
                    .minimumScaleFactor(0.7)
                    .privacySensitive()
                if headline.label != "Safe to spend", let safe = snapshot.safeToSpendMinor {
                    Text("safe \(snapshot.compact(safe))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .privacySensitive()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        case .accessoryInline:
            Text("\(snapshot.compact(headline.amountMinor)) left")
                .privacySensitive()
        default:  // circular and corner share the compact figure
            Text(snapshot.compact(headline.amountMinor))
                .font(.system(.body, design: .rounded).weight(.semibold))
                .minimumScaleFactor(0.5)
                .lineLimit(1)
                .privacySensitive()
                .widgetLabel(headline.label)
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
    }
}
