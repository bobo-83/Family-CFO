import Foundation

/// The daily-glance context behind the Overview tab (M88). Read-only: every
/// number here is computed server-side by the deterministic engine, so the
/// phone renders what the dashboard renders and cannot drift from it.
protocol HouseholdAPI: Sendable {
    /// The whole Overview for a month ("yyyy-MM") — live for the current month, a
    /// frozen snapshot for a past one. nil = current month.
    func context(month: String?) async throws -> Components.Schemas.HouseholdContext
    /// Transactions for a specific month ("yyyy-MM") — every one, so an older
    /// month's drill-down isn't truncated. Pass nil for the recent set.
    func transactions(month: String?) async throws -> [Components.Schemas.Transaction]
    /// Fetch new statements from every linked bank at once (the slow path), then
    /// auto-categorize. Distinct from `context()`, which only recomputes what's
    /// already stored. Returns the aggregate totals.
    func syncAll() async throws -> SyncTotals
    /// Spending by category for a month ("yyyy-MM"), or the current month when nil.
    func spending(month: String?) async throws -> Components.Schemas.SpendingByCategory
    /// The 30-day cash outlook (M112, ADR 0026): paychecks in, payments out, and
    /// the lowest point the balance reaches. A "now" concept — current month only.
    func cashOutlook() async throws -> Components.Schemas.CashOutlookResponse?
    /// Left to spend this month (M113, ADR 0027): expected income minus spent
    /// and committed. A "now" concept — current month only.
    func spendingPlan() async throws -> Components.Schemas.SpendingPlanResponse?
    /// The box running version (M120, ADR 0029) - compared against the app
    /// embedded version to surface "your app is stale, install the update".
    func serverVersion() async -> String?
    /// The year at a glance (M-yearly): monthly trend, totals, top categories,
    /// and the cached grounded review. nil year = the current year.
    func yearly(year: Int?) async throws -> Components.Schemas.YearlyOverview
    /// (Re)generate the year's narrative + suggestions on the box.
    func generateYearlyReview(year: Int?) async throws -> Components.Schemas.YearlyReview
    /// #203: state a contribution outright. Detection needs both legs in the
    /// ledger, so a destination that never syncs (a 529, a workplace plan) can
    /// never be found no matter how good the detector gets — the household has
    /// to say so, and its word outranks any detection on the same route.
    func declareSavingsContribution(
        _ request: Components.Schemas.SavingsContributionCreateRequest
    ) async throws
    /// #203: stop tracking a contribution the household declared.
    func deleteSavingsContribution(id: String) async throws
    /// #4: point a declared contribution at the goal it funds. A nil goal
    /// unlinks — the contribution keeps counting, it just funds nothing named.
    func updateSavingsContribution(id: String, goalID: String?) async throws
    /// #10: set the household's language (en, vi, lt) — household-wide, so the
    /// advisor answers everyone in it. The server 422s unsupported codes.
    func updateLanguage(_ language: String) async throws
    /// #5: reserve committed savings like a bill (true) or show it beside Safe
    /// to Spend without subtracting (false). Household-wide, gated on
    /// household.settings.manage — the right the PATCH checks.
    func updateReserveCommittedSavings(_ value: Bool) async throws
    /// #203: a detected route that isn't saving. Keyed by the route rather than
    /// a row id because detection re-derives its rows on every context load —
    /// there is no stable id to delete.
    func dismissSavingsContribution(
        sourceAccountID: String, destinationAccountID: String
    ) async throws
}

extension HouseholdAPI {
    /// Defaults so mocks/tests needn't implement them; the live client overrides.
    func cashOutlook() async throws -> Components.Schemas.CashOutlookResponse? { nil }
    func spendingPlan() async throws -> Components.Schemas.SpendingPlanResponse? { nil }
    func serverVersion() async -> String? { nil }
    func yearly(year: Int?) async throws -> Components.Schemas.YearlyOverview {
        Components.Schemas.YearlyOverview(
            year: year ?? 0, months: [],
            totalIncome: .init(amountMinor: 0, currency: "USD"),
            totalSpending: .init(amountMinor: 0, currency: "USD"),
            totalNet: .init(amountMinor: 0, currency: "USD"),
            topCategories: [])
    }
    func generateYearlyReview(year: Int?) async throws -> Components.Schemas.YearlyReview {
        throw APIError.server(503)
    }
    func declareSavingsContribution(
        _ request: Components.Schemas.SavingsContributionCreateRequest
    ) async throws {
        throw APIError.server(501)
    }
    func deleteSavingsContribution(id: String) async throws {
        throw APIError.server(501)
    }
    func updateSavingsContribution(id: String, goalID: String?) async throws {
        throw APIError.server(501)
    }
    func dismissSavingsContribution(
        sourceAccountID: String, destinationAccountID: String
    ) async throws {
        throw APIError.server(501)
    }
    func updateLanguage(_ language: String) async throws {
        throw APIError.server(501)
    }
    func updateReserveCommittedSavings(_ value: Bool) async throws {
        throw APIError.server(501)
    }
}

struct LiveHouseholdAPI: HouseholdAPI {
    let client: Client
    /// Fired with every LIVE (current-month) context this client fetches,
    /// whichever screen asked — AppModel seeds its household-language cache
    /// from the first one (#10). The app launches into the Advisor tab, so
    /// Overview's load alone would run too late for a read-aloud tap there.
    var onContext: (@MainActor @Sendable (Components.Schemas.HouseholdContext) -> Void)? = nil

    func yearly(year: Int?) async throws -> Components.Schemas.YearlyOverview {
        switch try await client.getYearlyOverview(.init(query: .init(year: year))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func generateYearlyReview(year: Int?) async throws -> Components.Schemas.YearlyReview {
        switch try await client.generateYearlyReview(.init(query: .init(year: year))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func context(month: String?) async throws -> Components.Schemas.HouseholdContext {
        switch try await client.getHouseholdContext(.init(query: .init(month: month))) {
        case .ok(let response):
            let context = try response.body.json
            // Only the live context speaks for household settings — a frozen
            // past-month snapshot may predate a language change.
            if month == nil, let onContext { await onContext(context) }
            return context
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func transactions(month: String?) async throws -> [Components.Schemas.Transaction] {
        switch try await client.listTransactions(.init(query: .init(month: month))) {
        case .ok(let response):
            return try response.body.json.transactions
        case .unauthorized:
            throw APIError.unauthorized
        case .unprocessableContent:
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func spending(month: String?) async throws -> Components.Schemas.SpendingByCategory {
        switch try await client.getSpendingByCategory(.init(query: .init(month: month))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .unprocessableContent:
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func cashOutlook() async throws -> Components.Schemas.CashOutlookResponse? {
        switch try await client.getCashOutlook(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            return nil
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func spendingPlan() async throws -> Components.Schemas.SpendingPlanResponse? {
        switch try await client.getSpendingPlan(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            return nil
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func serverVersion() async -> String? {
        // Best-effort: a version check must never break the Overview.
        guard case .ok(let response) = try? await client.getHealth(.init()),
            let health = try? response.body.json
        else { return nil }
        return health.version
    }

    func declareSavingsContribution(
        _ request: Components.Schemas.SavingsContributionCreateRequest
    ) async throws {
        switch try await client.declareSavingsContribution(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .code423:
            throw APIError.server(423)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteSavingsContribution(id: String) async throws {
        switch try await client.deleteSavingsContribution(
            .init(path: .init(contributionId: id))
        ) {
        // A row already gone is the outcome the tap asked for.
        case .noContent, .notFound:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateSavingsContribution(id: String, goalID: String?) async throws {
        let request = Components.Schemas.SavingsContributionUpdateRequest(goalId: goalID)
        switch try await client.updateSavingsContribution(
            .init(path: .init(contributionId: id), body: .json(request))
        ) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        // The contribution or the goal — either way the link can't be made.
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func dismissSavingsContribution(
        sourceAccountID: String, destinationAccountID: String
    ) async throws {
        let request = Components.Schemas.SavingsContributionDismissRequest(
            sourceAccountId: sourceAccountID, destinationAccountId: destinationAccountID)
        switch try await client.dismissSavingsContribution(.init(body: .json(request))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateLanguage(_ language: String) async throws {
        let request = Components.Schemas.HouseholdUpdateRequest(language: language)
        switch try await client.updateHousehold(.init(body: .json(request))) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        // A locale the box doesn't build; documented on the PATCH so the
        // client has a real case instead of an undocumented fall-through.
        case .unprocessableContent:
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateReserveCommittedSavings(_ value: Bool) async throws {
        let request = Components.Schemas.HouseholdUpdateRequest(reserveCommittedSavings: value)
        switch try await client.updateHousehold(.init(body: .json(request))) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        // Documented on the PATCH; a real case beats an undocumented fall-through.
        case .unprocessableContent:
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func syncAll() async throws -> SyncTotals {
        switch try await client.syncAllConnections(.init()) {
        case .ok(let response):
            let r = try response.body.json
            return SyncTotals(
                imported: r.imported,
                transfersFiled: r.transfersFiled ?? 0,
                autoCategorized: r.autoCategorized ?? 0
            )
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}

/// Selects and totals the transactions behind one category's monthly spend
/// (M94/M96), matching the server: everything filed under the category in the
/// month — outflows are spending, and a categorized inflow is a refund that nets
/// against it. Pure, so the filtering/ordering is testable.
enum CategorySpendingDetail {
    /// Pass `categoryID: nil` for the Uncategorized drill-down (outflows only,
    /// since a stray uncategorized inflow isn't spending).
    static func items(
        in transactions: [Components.Schemas.Transaction],
        categoryID: String?,
        month: String
    ) -> [Components.Schemas.Transaction] {
        transactions
            .filter { txn in
                guard txn.occurredAt.hasPrefix(month), txn.categoryId == categoryID else {
                    return false
                }
                // Categorized: purchases + refunds (which net). Uncategorized: outflows only.
                return categoryID != nil || txn.amount.amountMinor < 0
            }
            .sorted { $0.amount.amountMinor < $1.amount.amountMinor }  // biggest spend first, refunds last
    }

    static func total(_ items: [Components.Schemas.Transaction], currency: String) -> Components.Schemas.Money {
        .init(amountMinor: items.reduce(0) { $0 - $1.amount.amountMinor }, currency: currency)
    }

    /// One display row per purchase, pairing in a refund (same amount, ideally the
    /// same merchant) so a refunded purchase reads as a single struck-through
    /// entry rather than two loose lines. Leftover refunds show on their own.
    enum DisplayRow: Identifiable {
        case single(Components.Schemas.Transaction)
        case refunded(purchase: Components.Schemas.Transaction, refund: Components.Schemas.Transaction)

        var id: String {
            switch self {
            case .single(let txn): return txn.id
            case .refunded(let purchase, _): return purchase.id
            }
        }
    }

    static func grouped(_ items: [Components.Schemas.Transaction]) -> [DisplayRow] {
        var refunds = items.filter { $0.amount.amountMinor > 0 }
        var rows: [DisplayRow] = []
        for purchase in items where purchase.amount.amountMinor < 0 {
            let magnitude = -purchase.amount.amountMinor
            let match =
                refunds.firstIndex {
                    $0.amount.amountMinor == magnitude
                        && merchantsMatch($0.merchant, purchase.merchant)
                } ?? refunds.firstIndex { $0.amount.amountMinor == magnitude }
            if let index = match {
                rows.append(.refunded(purchase: purchase, refund: refunds.remove(at: index)))
            } else {
                rows.append(.single(purchase))
            }
        }
        rows.append(contentsOf: refunds.map { .single($0) })  // refunds with no purchase
        return rows
    }

    private static func merchantsMatch(_ a: String?, _ b: String?) -> Bool {
        let na = normalize(a)
        let nb = normalize(b)
        guard !na.isEmpty, !nb.isEmpty else { return false }
        return na == nb || na.hasPrefix(nb) || nb.hasPrefix(na)
    }

    private static func normalize(_ merchant: String?) -> String {
        (merchant ?? "").lowercased().filter(\.isLetter)
    }
}

// Money formatting lives in FamilyCFOShared/MoneyFormatting.swift (watch parity).
