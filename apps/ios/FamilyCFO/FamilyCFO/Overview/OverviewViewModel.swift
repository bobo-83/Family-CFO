import Foundation
import Observation

/// The Overview tab's state (M88). Read-only v1: it loads `GET /household` and
/// renders it. Every figure is the server's — the phone does no arithmetic of
/// its own, so it cannot disagree with the dashboard or the advisor.
@MainActor
@Observable
final class OverviewViewModel {
    private(set) var context: Components.Schemas.HouseholdContext?
    /// The 30-day cash outlook (M112) — a "now" concept, nil on historical months.
    private(set) var outlook: Components.Schemas.CashOutlookResponse?
    /// Left to spend this month (M113) — same "now" scoping as the outlook.
    private(set) var plan: Components.Schemas.SpendingPlanResponse?
    /// The box running version (M120) - nil until fetched or unreachable.
    private(set) var serverVersion: String?
    private(set) var isLoading = false
    private(set) var isSyncing = false
    private(set) var selectedMonth = MonthKey.current()
    var syncResult: String?
    var errorMessage: String?

    var isCurrentMonth: Bool { selectedMonth == MonthKey.current() }
    var monthLabel: String { MonthKey.label(selectedMonth) }
    /// Don't scroll past the oldest month with data ("YYYY-MM" compares lexically).
    /// False until a context has loaded, so you can't run past the cap mid-load.
    var canGoBack: Bool {
        guard let earliest = context?.earliestMonth else { return false }
        return selectedMonth > earliest
    }

    private let api: HouseholdAPI
    private let notifications: BillNotificationScheduler?
    private let snapshotStore: OverviewSnapshotStore?

    init(
        api: HouseholdAPI,
        notifications: BillNotificationScheduler? = BillNotificationScheduler(
            scheduler: SystemNotificationScheduler()),
        snapshotStore: OverviewSnapshotStore? = OverviewSnapshotStore()
    ) {
        self.api = api
        self.notifications = notifications
        self.snapshotStore = snapshotStore
    }

    /// `refreshable` and `task` both call this; the guard keeps a pull-to-
    /// refresh during the first load from firing a second request.
    func load() async {
        // Bind to the month requested at call time; a result the user has already
        // navigated away from is discarded rather than shown for the wrong month.
        let requested = selectedMonth
        let onCurrent = requested == MonthKey.current()
        isLoading = true
        defer { if selectedMonth == requested { isLoading = false } }
        do {
            async let outlookLoad = onCurrent ? api.cashOutlook() : nil
            async let planLoad = onCurrent ? api.spendingPlan() : nil
            let loaded = try await api.context(month: onCurrent ? nil : requested)
            let loadedOutlook = try await outlookLoad
            let loadedPlan = try await planLoad
            let version = await api.serverVersion()
            guard selectedMonth == requested else { return }
            serverVersion = version
            context = loaded
            outlook = loadedOutlook
            plan = loadedPlan
            errorMessage = nil
            // Reminders and the widget snapshot are "now" concepts — only refresh
            // them from the live current month, never from a historical one.
            if onCurrent {
                if let notifications, let bills = loaded.upcomingBills {
                    await notifications.refresh(from: bills)
                }
                if let snapshotStore {
                    snapshotStore.save(OverviewSnapshot(context: loaded, now: Date()))
                    WidgetRefresher.reloadOverview()
                }
            }
        } catch {
            guard selectedMonth == requested else { return }
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Step the whole Overview to another month. Next is capped at the current
    /// month (there is no future to show).
    func shiftMonth(_ delta: Int) async {
        if delta > 0 && isCurrentMonth { return }  // no future
        if delta < 0 && !canGoBack { return }  // no data before the earliest month
        guard let month = MonthKey.shift(selectedMonth, by: delta) else { return }
        selectedMonth = month
        await load()
    }

    /// Reload the selected month — used after an in-place recategorize.
    func reload() async { await load() }

    /// The slow path: fetch new statements from the banks, then recompute. Pull-to-
    /// refresh only recomputes what's stored; this is how new bank data arrives.
    func syncNow() async {
        guard !isSyncing else { return }
        isSyncing = true
        defer { isSyncing = false }
        syncResult = nil
        do {
            let totals = try await api.syncAll()
            syncResult = BillsViewModel.syncSummary(totals)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The version this build was stamped with (the monorepo VERSION file, via
    /// MARKETING_VERSION at build time - M120, ADR 0029).
    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
    }

    /// True when the box runs a different version than this build - the app is
    /// stale (or the box is), and the OTA page has the fix.
    var versionMismatch: Bool {
        guard let serverVersion else { return false }
        return serverVersion != Self.appVersion
    }

    /// "Last synced 3 hours ago" for the freshness line, or nil when never synced.
    var lastSyncedText: String? {
        guard let date = context?.lastSyncedAt else { return nil }
        let elapsed = RelativeDateTimeFormatter()
        elapsed.unitsStyle = .full
        return "Last synced " + elapsed.localizedString(for: date, relativeTo: Date())
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
