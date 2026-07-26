import Foundation
import Observation

/// Budgets state (M118): the month's envelopes with progress, plus the
/// categories that don't have one yet (candidates for a new envelope).
@MainActor
@Observable
final class BudgetsViewModel {
    private(set) var budgets: [Components.Schemas.Budget] = []
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: BudgetsAPI

    init(api: BudgetsAPI) {
        self.api = api
    }

    /// The whole month's envelope picture (user request 2026-07-25): total
    /// budgeted, total spent, burn percent — the phone/watch/web summary
    /// strips all use this same arithmetic.
    var summary: (limit: String, spent: String, percentUsed: Int)? {
        guard let currency = budgets.first?.limit.currency else { return nil }
        let limit = budgets.reduce(Int64(0)) { $0 + Int64($1.limit.amountMinor) }
        guard limit > 0 else { return nil }
        let spent = budgets.reduce(Int64(0)) { $0 + Int64($1.spent.amountMinor) }
        func money(_ minor: Int64) -> String {
            (Decimal(minor) / 100).formatted(.currency(code: currency).precision(.fractionLength(0)))
        }
        return (
            money(limit), money(spent),
            Int((Double(spent) / Double(limit) * 100).rounded())
        )
    }

    /// Categories without an envelope yet — the add sheet's choices.
    var unbudgetedCategories: [Components.Schemas.Category] {
        let budgeted = Set(budgets.map(\.categoryId))
        return categories.filter { !budgeted.contains($0.id) }
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let list = api.budgets()
            async let cats = api.categories()
            budgets = try await list
            categories = try await cats
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func create(categoryID: String, limitMinor: Int64, currency: String = "USD") async {
        guard limitMinor > 0 else { return }
        do {
            try await api.createBudget(
                categoryID: categoryID, limitMinor: limitMinor, currency: currency)
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func updateLimit(_ budget: Components.Schemas.Budget, limitMinor: Int64) async {
        guard limitMinor > 0 else { return }
        do {
            try await api.updateBudget(
                id: budget.id, limitMinor: limitMinor, currency: budget.limit.currency)
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func delete(_ budget: Components.Schemas.Budget) async {
        guard let index = budgets.firstIndex(where: { $0.id == budget.id }) else { return }
        budgets.remove(at: index)
        do {
            try await api.deleteBudget(id: budget.id)
            errorMessage = nil
        } catch {
            budgets.insert(budget, at: min(index, budgets.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
