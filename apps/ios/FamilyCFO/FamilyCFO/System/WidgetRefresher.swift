import Foundation
import WidgetKit

/// Nudges the home-screen widget to re-read the shared snapshot (M92a) after the
/// app writes fresh values. A no-op on OSes without WidgetKit, and harmless when
/// no widget is installed.
enum WidgetRefresher {
    static func reloadOverview() {
        WidgetCenter.shared.reloadTimelines(ofKind: OverviewSnapshot.widgetKind)
    }
}
