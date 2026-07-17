import Foundation

/// Holds a month's transactions + the category list so spending-category
/// drill-downs read from memory instead of re-fetching (M105). The cache is
/// filled ONLY by an explicit Overview action — its initial load and
/// pull-to-refresh — never automatically. Drill-downs are pure reads; they don't
/// hit the server (a cold cache is a rare fallback, e.g. before Overview has run).
@MainActor
final class MonthTransactionsCache {
    private var byMonth: [String: [Components.Schemas.Transaction]] = [:]
    private var categoryList: [Components.Schemas.Category]?

    /// Explicitly (re)load a month and the categories from the server. Called by
    /// Overview on its load and on pull-to-refresh. Keeps the prior data on error.
    func reload(
        month: String,
        transactions: () async throws -> [Components.Schemas.Transaction],
        categories: () async throws -> [Components.Schemas.Category]
    ) async {
        do {
            async let txns = transactions()
            async let cats = categories()
            byMonth[month] = try await txns
            categoryList = try await cats
        } catch {
            // Keep whatever we already had; the drill-down falls back if empty.
        }
    }

    /// Store a month's data directly (a drill-down's cold-cache fallback fill).
    func store(
        month: String,
        transactions: [Components.Schemas.Transaction],
        categories: [Components.Schemas.Category]
    ) {
        byMonth[month] = transactions
        categoryList = categories
    }

    /// The cached month + categories, or nil if nothing has been loaded. Read-only:
    /// this never fetches.
    func cached(
        month: String
    ) -> (transactions: [Components.Schemas.Transaction], categories: [Components.Schemas.Category])? {
        guard let txns = byMonth[month], let cats = categoryList else { return nil }
        return (txns, cats)
    }

    /// Drop everything — after a recategorize/delete or a sync changed the data.
    func invalidate() {
        byMonth.removeAll()
        categoryList = nil
    }
}
