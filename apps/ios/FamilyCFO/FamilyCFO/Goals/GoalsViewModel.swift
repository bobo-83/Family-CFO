import Foundation
import Observation

/// Goals state (M119): every goal with progress, plus create/edit/delete.
@MainActor
@Observable
final class GoalsViewModel {
    private(set) var goals: [Components.Schemas.Goal] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: GoalsAPI
    /// #156: the household's base currency, for NEW goals. nil (an older
    /// preview or mock) leaves the Add button closed.
    private let currencyProvider: HouseholdCurrencyProvider?

    init(api: GoalsAPI, currency: HouseholdCurrencyProvider? = nil) {
        self.api = api
        self.currencyProvider = currency
    }

    /// #156 (ADR 0075): a new goal is declared in the base currency — never a
    /// literal "USD". An existing goal keeps the currency it was declared in.
    var baseCurrency: String? { currencyProvider?.current }

    func loadCurrency() async {
        _ = try? await currencyProvider?.resolve()
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            goals = try await api.goals()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func create(_ request: Components.Schemas.GoalCreateRequest) async {
        do {
            try await api.createGoal(request)
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func update(id: String, _ request: Components.Schemas.GoalUpdateRequest) async {
        do {
            try await api.updateGoal(id: id, request)
            await load()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func delete(_ goal: Components.Schemas.Goal) async {
        guard let index = goals.firstIndex(where: { $0.id == goal.id }) else { return }
        goals.remove(at: index)
        do {
            try await api.deleteGoal(id: goal.id)
            errorMessage = nil
        } catch {
            goals.insert(goal, at: min(index, goals.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Progress toward the target, clamped to 0...1 (nil target → no bar).
    static func progress(_ goal: Components.Schemas.Goal) -> Double? {
        guard goal.target.amountMinor > 0 else { return nil }
        return min(max(Double(goal.current.amountMinor) / Double(goal.target.amountMinor), 0), 1)
    }
}
