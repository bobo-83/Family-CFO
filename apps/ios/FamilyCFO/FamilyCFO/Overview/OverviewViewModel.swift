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
    private(set) var outlookErrorMessage: String?
    private(set) var planErrorMessage: String?
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

    /// #156 (ADR 0075): the accounts a base-currency total leaves out, as one
    /// line — "Not counted in USD totals: Euro Savings (€4,000.00) · …" — or
    /// nil when there are none. `nil` on the wire is a past month whose
    /// accounts are unknown and `[]` is known-and-none; neither gets a note.
    static func outsideBaseCurrencyNote(
        _ context: Components.Schemas.HouseholdContext
    ) -> String? {
        let outside = context.accountsOutsideBaseCurrency ?? []
        guard !outside.isEmpty else { return nil }
        let list = outside.map { "\($0.name) (\($0.balance.formattedExact))" }
            .joined(separator: " · ")
        return String(localized: "Not counted in \(context.currency) totals: \(list)")
    }
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
    private let ownerIdentity: @MainActor () -> String?
    /// Identity of the API instance itself. The dynamic owner may change after
    /// re-pairing; an old client must never be relabeled as the new session.
    private let apiIdentity: String?
    private let notificationScope: String

    struct LoadOwner: Equatable {
        let identity: String
        let month: String
        let generation: UInt64
    }

    private var generation: UInt64 = 0
    private var currentOwner: LoadOwner?
    private var syncGeneration: UInt64 = 0
    private var activeSyncGeneration: UInt64?
    private var outlookTask: Task<Components.Schemas.CashOutlookResponse?, Error>?
    private var planTask: Task<Components.Schemas.SpendingPlanResponse?, Error>?
    private var versionTask: Task<String?, Never>?

    init(
        api: HouseholdAPI,
        goalsAPI: GoalsAPI? = nil,
        notifications: BillNotificationScheduler? = BillNotificationScheduler(
            scheduler: SystemNotificationScheduler()),
        snapshotStore: OverviewSnapshotStore? = OverviewSnapshotStore(),
        ownerIdentity: @escaping @MainActor () -> String? = { "standalone" },
        apiIdentity: String? = nil,
        notificationScope: String = UUID().uuidString
    ) {
        self.api = api
        self.goalsAPI = goalsAPI
        self.notifications = notifications
        self.snapshotStore = snapshotStore
        self.ownerIdentity = ownerIdentity
        self.apiIdentity = apiIdentity ?? ownerIdentity()
        self.notificationScope = notificationScope
    }

    /// Latest owner wins even for two requests for the same month. Context is
    /// authoritative and commits independently; current-only outlook/plan are
    /// optional sections whose failures never turn a valid context into a page
    /// error.
    @discardableResult
    func load() async -> LoadOwner? {
        generation &+= 1
        outlookTask?.cancel()
        planTask?.cancel()
        versionTask?.cancel()

        let requested = selectedMonth
        guard let identity = ownerIdentity(), identity == apiIdentity else {
            currentOwner = nil
            isLoading = false
            return nil
        }
        let owner = LoadOwner(identity: identity, month: requested, generation: generation)
        currentOwner = owner
        let onCurrent = requested == MonthKey.current()
        isLoading = true
        outlookErrorMessage = nil
        planErrorMessage = nil
        if !onCurrent {
            outlook = nil
            plan = nil
        }
        defer {
            if owns(owner) { isLoading = false }
        }

        if onCurrent {
            outlookTask = Task { try await api.cashOutlook() }
            planTask = Task { try await api.spendingPlan() }
        } else {
            outlookTask = nil
            planTask = nil
        }
        versionTask = Task { await api.serverVersion() }

        let loaded: Components.Schemas.HouseholdContext
        do {
            loaded = try await api.context(month: onCurrent ? nil : requested)
        } catch {
            guard owns(owner), !Task.isCancelled else { return nil }
            outlookTask?.cancel()
            outlookTask = nil
            planTask?.cancel()
            planTask = nil
            context = nil
            outlook = nil
            plan = nil
            goalNames = [:]
            outlookErrorMessage = nil
            planErrorMessage = nil
            errorMessage = ChatViewModel.describe(error)
            if onCurrent, let snapshotStore {
                guard owns(owner), !Task.isCancelled else { return nil }
                snapshotStore.clear()
                guard owns(owner), !Task.isCancelled else { return nil }
                WidgetRefresher.reloadOverview()
            }
            return nil
        }
        guard owns(owner), !Task.isCancelled else { return nil }
        context = loaded
        errorMessage = nil

        if let versionTask {
            let version = await versionTask.value
            guard owns(owner), !Task.isCancelled else { return nil }
            serverVersion = version
        }

        if let outlookTask {
            do {
                let value = try await outlookTask.value
                guard owns(owner), !Task.isCancelled else { return nil }
                outlook = value
            } catch {
                guard owns(owner), !Task.isCancelled else { return nil }
                outlook = nil
                outlookErrorMessage = ChatViewModel.describe(error)
            }
        }
        if let planTask {
            do {
                let value = try await planTask.value
                guard owns(owner), !Task.isCancelled else { return nil }
                plan = value
            } catch {
                guard owns(owner), !Task.isCancelled else { return nil }
                plan = nil
                planErrorMessage = ChatViewModel.describe(error)
            }
        }

        await resolveGoalNames(for: loaded, owner: owner)
        guard owns(owner), !Task.isCancelled else { return nil }

        // Reminders and primitive caches are current-context side effects. The
        // scheduler rechecks this owner after each of its own suspension points.
        if onCurrent {
            if let notifications, let bills = loaded.upcomingBills {
                await notifications.refresh(
                    from: bills,
                    scope: "\(notificationScope).\(owner.generation)"
                ) { [weak self] in
                    await self?.owns(owner) == true
                }
                guard owns(owner), !Task.isCancelled else { return nil }
            }
            if let snapshotStore {
                guard owns(owner), !Task.isCancelled else { return nil }
                snapshotStore.save(OverviewSnapshot(context: loaded, now: Date()))
                guard owns(owner), !Task.isCancelled else { return nil }
                WidgetRefresher.reloadOverview()
            }
        }
        return owns(owner) && !Task.isCancelled ? owner : nil
    }

    func owns(_ owner: LoadOwner) -> Bool {
        currentOwner == owner && ownerIdentity() == owner.identity && selectedMonth == owner.month
    }

    /// Step the whole Overview to another month. Next is capped at the current
    /// month (there is no future to show).
    @discardableResult
    func shiftMonth(_ delta: Int) async -> LoadOwner? {
        if delta > 0 && isCurrentMonth { return nil }  // no future
        if delta < 0 && !canGoBack { return nil }  // no data before the earliest month
        guard let month = MonthKey.shift(selectedMonth, by: delta) else { return nil }
        selectedMonth = month
        return await load()
    }

    /// Reload the selected month — used after an in-place recategorize.
    @discardableResult
    func reload() async -> LoadOwner? { await load() }

    /// Jump straight to a month ("yyyy-MM") — the Year view's drill-down.
    @discardableResult
    func show(month: String) async -> LoadOwner? {
        guard month != selectedMonth else { return nil }
        selectedMonth = min(month, MonthKey.current())
        return await load()
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
    private func resolveGoalNames(
        for context: Components.Schemas.HouseholdContext, owner: LoadOwner
    ) async {
        let referenced = context.savingsContributions.contributions
            .flatMap { [$0.goalId, $0.suggestedGoalId] }
            .compactMap { $0 }
        guard !referenced.isEmpty, let goalsAPI else {
            guard owns(owner), !Task.isCancelled else { return }
            goalNames = [:]
            return
        }
        guard let goals = try? await goalsAPI.goals(), owns(owner), !Task.isCancelled else { return }
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
    @discardableResult
    func syncNow() async -> LoadOwner? {
        guard !isSyncing, let identity = ownerIdentity(), identity == apiIdentity else {
            return nil
        }
        let requestedMonth = selectedMonth
        syncGeneration &+= 1
        let syncOwner = syncGeneration
        activeSyncGeneration = syncOwner
        isSyncing = true
        defer {
            // Month/session changes make the result stale, but they must not
            // strand the spinner. Only a newer sync may retain ownership.
            if activeSyncGeneration == syncOwner {
                activeSyncGeneration = nil
                isSyncing = false
            }
        }
        syncResult = nil
        do {
            let totals = try await api.syncAll()
            guard activeSyncGeneration == syncOwner, ownerIdentity() == identity,
                selectedMonth == requestedMonth, !Task.isCancelled
            else { return nil }
            syncResult = BillsViewModel.syncSummary(totals)
            errorMessage = nil
            return await load()
        } catch {
            guard activeSyncGeneration == syncOwner, ownerIdentity() == identity,
                selectedMonth == requestedMonth, !Task.isCancelled
            else { return nil }
            errorMessage = ChatViewModel.describe(error)
            return nil
        }
    }

    /// The version this build was stamped with - "<contract>.<build>", e.g.
    /// "0.157.1", composed by the deploy scripts into MARKETING_VERSION
    /// (M120, ADR 0029 as amended by ADR 0074).
    static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
    }

    /// The compatibility contract - the MAJOR.MINOR prefix of a complete
    /// MAJOR.MINOR.BUILD artifact version (ADR 0074). Invalid runtime values
    /// are unverifiable, never compatible. Mirrors the dashboard's strict
    /// parser rather than the shell helper, which also accepts bare contracts
    /// because it operates on the repository's /VERSION file.
    static func contract(of version: String) -> String? {
        guard version.wholeMatch(of: /^[0-9]+\.[0-9]+\.[0-9]+$/) != nil else { return nil }
        return version.split(separator: ".").prefix(2).joined(separator: ".")
    }

    /// Whether two valid artifact versions name different contracts. nil means
    /// at least one value is malformed and compatibility cannot be verified.
    static func versionsDiffer(app: String, box: String) -> Bool? {
        guard let appContract = contract(of: app), let boxContract = contract(of: box) else {
            return nil
        }
        return appContract != boxContract
    }

    private var versionDifference: Bool? {
        guard let serverVersion else { return nil }
        return Self.versionsDiffer(app: Self.appVersion, box: serverVersion)
    }

    /// True when the box speaks a different contract than this build - the app
    /// is stale (or the box is), and the OTA page has the fix. The guard keeps
    /// a half-known pair (box unreachable) from being called a mismatch.
    var versionMismatch: Bool {
        versionDifference == true
    }

    /// A responding box supplied a malformed version (or this build was
    /// stamped incorrectly). Unlike an unreachable box, that is actionable and
    /// must be surfaced rather than silently treated as compatible.
    var versionUnverifiable: Bool {
        serverVersion != nil && versionDifference == nil
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
        case .unavailable: return unavailableValueText
        }
    }

    /// Progress toward the recommended target, clamped to 0...1. Nil when the
    /// server has no bills to size the fund against, in which case there is no
    /// honest denominator and the view shows no bar.
    var progressToRecommended: Double? {
        guard status != .noBills, status != .unavailable,
            targetMonthsRecommended > 0, let months
        else { return nil }
        return min(max(months / targetMonthsRecommended, 0), 1)
    }
}
