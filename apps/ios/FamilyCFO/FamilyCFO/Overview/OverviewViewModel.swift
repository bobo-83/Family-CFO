import Foundation
import Observation

/// The Overview tab's state (M88). Read-only v1: it loads `GET /household` and
/// renders it. Every figure is the server's — the phone does no arithmetic of
/// its own, so it cannot disagree with the dashboard or the advisor.
@MainActor
@Observable
final class OverviewViewModel {
    private(set) var context: Components.Schemas.HouseholdContext?
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: HouseholdAPI
    private let notifications: BillNotificationScheduler?

    init(
        api: HouseholdAPI,
        notifications: BillNotificationScheduler? = BillNotificationScheduler(
            scheduler: SystemNotificationScheduler())
    ) {
        self.api = api
        self.notifications = notifications
    }

    /// `refreshable` and `task` both call this; the guard keeps a pull-to-
    /// refresh during the first load from firing a second request.
    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let context = try await api.context()
            self.context = context
            errorMessage = nil
            // Refresh bill reminders from the freshly-loaded context (M92c) —
            // no separate poll of the box; this data was already fetched.
            if let notifications, let bills = context.upcomingBills {
                await notifications.refresh(from: bills)
            }
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}

/// Presentation for the emergency-fund status (M38's enum), kept out of the
/// view so it is testable.
extension Components.Schemas.EmergencyFundSummary {
    var statusLabel: String {
        switch status {
        case .noBills: return "Add bills to size your fund"
        case .noFund: return "Not started"
        case .gettingStarted: return "Getting started"
        case .onTrack: return "On track"
        case .fullyFunded: return "Fully funded"
        }
    }

    /// Progress toward the recommended target, clamped to 0...1. Nil when the
    /// server has no bills to size the fund against, in which case there is no
    /// honest denominator and the view shows no bar.
    var progressToRecommended: Double? {
        guard status != .noBills, targetMonthsRecommended > 0, let months else { return nil }
        return min(max(months / targetMonthsRecommended, 0), 1)
    }
}
