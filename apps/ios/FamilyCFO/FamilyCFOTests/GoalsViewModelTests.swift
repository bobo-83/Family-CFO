import Foundation
import Testing

@testable import FamilyCFO

/// #156 (ADR 0075): a new goal is declared in the household's base currency;
/// an existing goal keeps the currency it was declared in.
@MainActor
struct GoalsViewModelTests {
    private func provider(_ currency: String) -> HouseholdCurrencyProvider {
        HouseholdCurrencyProvider(sessionKey: { "hh-1:device:token" }, fetch: { currency })
    }

    @Test func theBaseCurrencyIsUnknownUntilLoaded() async {
        let viewModel = GoalsViewModel(api: MockGoalsAPI(), currency: provider("VND"))
        #expect(viewModel.baseCurrency == nil)
        await viewModel.loadCurrency()
        #expect(viewModel.baseCurrency == "VND")
    }

    @Test func withoutAProviderTheBaseCurrencyStaysUnknown() async {
        let viewModel = GoalsViewModel(api: MockGoalsAPI())
        await viewModel.loadCurrency()
        #expect(viewModel.baseCurrency == nil)
    }

    @Test func aNewGoalCarriesTheBaseCurrency() async {
        let api = MockGoalsAPI()
        let viewModel = GoalsViewModel(api: api, currency: provider("VND"))
        await viewModel.loadCurrency()
        let currency = viewModel.baseCurrency ?? "?"

        await viewModel.create(
            .init(
                name: "Tet trip", _type: .vacation,
                target: .init(amountMinor: 500_000, currency: currency),
                priority: 2,
                monthlyContribution: .init(amountMinor: 10_000, currency: currency)))

        #expect(api.created.count == 1)
        #expect(api.created.first?.target.currency == "VND")
        #expect(api.created.first?.monthlyContribution?.currency == "VND")
    }

    @Test func anExistingGoalIsEditedInItsOwnCurrency() async {
        let api = MockGoalsAPI()
        let viewModel = GoalsViewModel(api: api, currency: provider("USD"))
        let eurGoal = Components.Schemas.Goal(
            id: "g-eur", name: "Paris", _type: .vacation,
            target: .init(amountMinor: 900_000, currency: "EUR"),
            current: .init(amountMinor: 0, currency: "EUR"), priority: 1)

        // What GoalsView sends for an edit: the goal's declared currency, whatever the base.
        await viewModel.update(
            id: eurGoal.id,
            .init(
                name: "Paris", target: .init(amountMinor: 950_000, currency: eurGoal.target.currency),
                priority: 1,
                monthlyContribution: .init(amountMinor: 15_000, currency: eurGoal.target.currency)))

        #expect(api.updated.count == 1)
        #expect(api.updated.first?.request.target?.currency == "EUR")
        #expect(api.updated.first?.request.monthlyContribution?.currency == "EUR")
    }
}
