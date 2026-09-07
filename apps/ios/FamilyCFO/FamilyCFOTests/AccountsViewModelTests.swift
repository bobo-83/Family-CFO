import Foundation
import Testing

@testable import FamilyCFO

/// #156 (ADR 0075): the Accounts tab takes the household's base currency from
/// the household, never from the first account or a literal "USD"; the
/// reservation total is base-currency only; a foreign account is listed and
/// marked, never summed.
@MainActor
struct AccountsViewModelTests {
    private func money(_ minor: Int64, _ currency: String = "USD") -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: currency)
    }

    private func account(
        _ id: String, _ name: String, type: Components.Schemas.AccountType = .savings,
        balance: Components.Schemas.Money, reserved: Components.Schemas.Money? = nil
    ) -> Components.Schemas.Account {
        .init(
            id: id, name: name, _type: type, balance: balance,
            emergencyFundPercent: reserved == nil ? nil : 100,
            emergencyFundReserved: reserved)
    }

    private func provider(_ currency: String?) -> HouseholdCurrencyProvider {
        HouseholdCurrencyProvider(
            sessionKey: { "hh-1:device:token" },
            fetch: {
                guard let currency else { throw HouseholdCurrencyProviderTests.Boom() }
                return currency
            })
    }

    @Test func theCurrencyIsUnknownUntilLoadedAndNeverGuessedFromTheAccounts() async {
        let api = MockAccountsAPI()
        api.currentAccounts = [account("a1", "Euro Savings", balance: money(400_000, "EUR"))]
        let viewModel = AccountsViewModel(api: api, currency: provider("USD"))
        await viewModel.load()

        #expect(viewModel.baseCurrency == nil)
        await viewModel.loadCurrency()
        #expect(viewModel.baseCurrency == "USD")
    }

    @Test func aManualAccountIsCreatedInTheBaseCurrency() async {
        let api = MockAccountsAPI()
        let viewModel = AccountsViewModel(api: api, currency: provider("EUR"))

        await viewModel.addAccount(name: "Sparkonto", type: .savings, balanceMinor: 12_300)

        #expect(api.created.count == 1)
        #expect(api.created.first?.currency == "EUR")
        #expect(viewModel.errorMessage == nil)
    }

    @Test func nothingIsSavedWhileTheCurrencyCannotBeResolved() async {
        let api = MockAccountsAPI()
        let viewModel = AccountsViewModel(api: api, currency: provider(nil))

        await viewModel.addAccount(name: "Sparkonto", type: .savings, balanceMinor: 12_300)

        #expect(api.created.isEmpty)
        #expect(viewModel.errorMessage != nil)
        #expect(viewModel.currencyError != nil)
    }

    @Test func withoutAProviderTheAddPathStaysClosed() async {
        let api = MockAccountsAPI()
        let viewModel = AccountsViewModel(api: api)

        await viewModel.addAccount(name: "Sparkonto", type: .savings, balanceMinor: 12_300)

        #expect(viewModel.baseCurrency == nil)
        #expect(api.created.isEmpty)
    }

    @Test func reservationTotalIsBaseCurrencyOnlyAndTheForeignOneIsDisclosed() async {
        let api = MockAccountsAPI()
        api.currentAccounts = [
            account("a1", "HY Savings", balance: money(1_000_000), reserved: money(500_000)),
            account(
                "eu1", "Euro Savings", balance: money(400_000, "EUR"),
                reserved: money(400_000, "EUR")),
        ]
        let viewModel = AccountsViewModel(api: api, currency: provider("USD"))
        await viewModel.load()
        await viewModel.loadCurrency()

        #expect(viewModel.emergencyFundTotal == money(500_000))
        #expect(viewModel.foreignReservations.map(\.name) == ["Euro Savings"])
        #expect(viewModel.foreignReservations.first?.reserved == money(400_000, "EUR"))
        #expect(viewModel.isOutsideBaseCurrency(api.currentAccounts[1]))
        #expect(!viewModel.isOutsideBaseCurrency(api.currentAccounts[0]))
    }

    @Test func onlyForeignReservationsLeaveTheTotalNilButStillDisclosed() async {
        let api = MockAccountsAPI()
        api.currentAccounts = [
            account("eu1", "Euro Savings", balance: money(400_000, "EUR"), reserved: money(400_000, "EUR"))
        ]
        let viewModel = AccountsViewModel(api: api, currency: provider("USD"))
        await viewModel.load()
        await viewModel.loadCurrency()

        #expect(viewModel.emergencyFundTotal == nil)
        #expect(viewModel.foreignReservations.count == 1)
    }

    @Test func nothingIsMarkedBeforeTheCurrencyIsKnown() async {
        let api = MockAccountsAPI()
        api.currentAccounts = [account("eu1", "Euro Savings", balance: money(400_000, "EUR"))]
        let viewModel = AccountsViewModel(api: api, currency: provider("USD"))
        await viewModel.load()

        #expect(!viewModel.isOutsideBaseCurrency(api.currentAccounts[0]))
        #expect(viewModel.emergencyFundTotal == nil)
        #expect(viewModel.foreignReservations.isEmpty)
    }
}
