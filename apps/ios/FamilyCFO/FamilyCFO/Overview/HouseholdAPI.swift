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
}

struct LiveHouseholdAPI: HouseholdAPI {
    let client: Client

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
            return try response.body.json
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
