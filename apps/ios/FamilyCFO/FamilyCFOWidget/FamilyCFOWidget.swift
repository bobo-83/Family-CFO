import SwiftUI
import WidgetKit

/// Home-screen widget (M92a): net worth + emergency-fund status at a glance.
/// It reads ONLY the cached snapshot the app wrote to the shared App Group
/// container — it never polls the box (battery, and the box may be off-network).
/// Stale is fine and shown honestly ("as of …").

struct OverviewEntry: TimelineEntry {
    let date: Date
    let snapshot: OverviewSnapshot?
}

struct OverviewProvider: TimelineProvider {
    private let store = OverviewSnapshotStore()

    func placeholder(in context: Context) -> OverviewEntry {
        OverviewEntry(
            date: Date(),
            snapshot: OverviewSnapshot(
                netWorthMinor: 1_234_500, currency: "USD",
                emergencyFundStatus: "On track", emergencyFundMonths: 4.5, capturedAt: Date()))
    }

    func getSnapshot(in context: Context, completion: @escaping (OverviewEntry) -> Void) {
        completion(OverviewEntry(date: Date(), snapshot: store.load()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<OverviewEntry>) -> Void) {
        // One entry: the app refreshes the timeline whenever it writes new
        // values, so the widget doesn't need its own schedule. A daily refresh
        // is a safety net so the "as of" age stays roughly current.
        let entry = OverviewEntry(date: Date(), snapshot: store.load())
        let next = Calendar.current.date(byAdding: .day, value: 1, to: Date()) ?? Date()
        completion(Timeline(entries: [entry], policy: .after(next)))
    }
}

struct OverviewWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: OverviewEntry

    var body: some View {
        if let snapshot = entry.snapshot {
            content(snapshot)
        } else {
            unpaired
        }
    }

    private func content(_ snapshot: OverviewSnapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Net worth")
                .font(.caption2)
                .foregroundStyle(.secondary)
            Text(snapshot.netWorthFormatted)
                .font(.system(family == .systemSmall ? .title2 : .title, design: .rounded).weight(.semibold))
                .minimumScaleFactor(0.6)
                .lineLimit(1)

            Spacer(minLength: 2)

            HStack(spacing: 4) {
                Image(systemName: "umbrella.fill")
                    .font(.caption2)
                    .foregroundStyle(.tint)
                Text(snapshot.emergencyFundStatus)
                    .font(.caption)
                    .lineLimit(1)
            }
            if let months = snapshot.emergencyFundMonths {
                Text("\(months, specifier: "%.1f") months covered")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Text("as of \(snapshot.capturedAt, format: .relative(presentation: .named))")
                .font(.system(size: 9))
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    private var unpaired: some View {
        VStack(spacing: 6) {
            Image(systemName: "iphone.slash").foregroundStyle(.secondary)
            Text("Open Family CFO to pair")
                .font(.caption2)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct FamilyCFOOverviewWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: OverviewSnapshot.widgetKind, provider: OverviewProvider()) { entry in
            OverviewWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Net worth")
        .description("Your household net worth and emergency-fund status at a glance.")
        .supportedFamilies([.systemSmall, .systemMedium])
    }
}

@main
struct FamilyCFOWidgetBundle: WidgetBundle {
    var body: some Widget {
        FamilyCFOOverviewWidget()
    }
}
