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
    /// #4: goal names by id, for savings rows that fund (or could fund) one.
    /// Filled lazily — one goals-list fetch when a loaded contribution
    /// references a goal, never a per-row lookup.
    private(set) var goalNames: [String: String] = [:]
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
    /// #4: only for naming the goals savings contributions reference; nil
    /// (an unpaired preview, an older mock) just leaves the names off.
    private let goalsAPI: GoalsAPI?
    private let notifications: BillNotificationScheduler?
    private let snapshotStore: OverviewSnapshotStore?

    init(
        api: HouseholdAPI,
        goalsAPI: GoalsAPI? = nil,
        notifications: BillNotificationScheduler? = BillNotificationScheduler(
            scheduler: SystemNotificationScheduler()),
        snapshotStore: OverviewSnapshotStore? = OverviewSnapshotStore()
    ) {
        self.api = api
        self.goalsAPI = goalsAPI
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
            await resolveGoalNames()
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

    /// Jump straight to a month ("yyyy-MM") — the Year view's drill-down.
    func show(month: String) async {
        guard month != selectedMonth else { return }
        selectedMonth = min(month, MonthKey.current())
        await load()
    }

    /// #203: record a contribution the household knows about and the ledger
    /// can't show. Answers whether it stuck so the sheet can keep what was typed
    /// on screen when the server refuses, instead of dropping it.
    func declareContribution(
        _ request: Components.Schemas.SavingsContributionCreateRequest
    ) async -> Bool {
        do {
            try await api.declareSavingsContribution(request)
            await load()
            return true
        } catch {
            errorMessage = ChatViewModel.describe(error)
            return false
        }
    }

    /// #203: the household's own row, withdrawn.
    func stopTracking(contributionID: String) async {
        do {
            try await api.deleteSavingsContribution(id: contributionID)
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// #4: point a declared contribution at the goal it funds — or at nil,
    /// which unlinks. The refresh brings back both the row's new link and the
    /// goal's funding line elsewhere.
    func linkContribution(contributionID: String, goalID: String?) async {
        do {
            try await api.updateSavingsContribution(id: contributionID, goalID: goalID)
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// #4: fetch goal names once per load, and only when a contribution
    /// actually references a goal. Best-effort — the names decorate the
    /// savings card, so their absence must never break the Overview.
    private func resolveGoalNames() async {
        let referenced = (context?.savingsContributions ?? [])
            .flatMap { [$0.goalId, $0.suggestedGoalId] }
            .compactMap { $0 }
        guard !referenced.isEmpty, let goalsAPI else { return }
        guard let goals = try? await goalsAPI.goals() else { return }
        goalNames = Dictionary(
            goals.map { ($0.id, $0.name) }, uniquingKeysWith: { first, _ in first })
    }

    /// #203: "that transfer isn't saving". Suppresses the route, not one row —
    /// detection would otherwise re-derive it on the next load.
    func dismissRoute(sourceAccountID: String, destinationAccountID: String) async {
        do {
            try await api.dismissSavingsContribution(
                sourceAccountID: sourceAccountID, destinationAccountID: destinationAccountID)
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

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
        let ago = elapsed.localizedString(for: date, relativeTo: Date())
        return String(localized: "Last synced \(ago)")
    }
}

/// Presentation for the emergency-fund status (M38's enum), kept out of the
/// view so it is testable.
extension Components.Schemas.EmergencyFundSummary {
    var statusLabel: String {
        switch status {
        case .noBills: return String(localized: "Add bills to size your fund")
        case .noFund: return String(localized: "Not started")
        case .gettingStarted: return String(localized: "Getting started")
        case .onTrack: return String(localized: "On track")
        case .fullyFunded: return String(localized: "Fully funded")
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
