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

    /// A provider whose first fetches fail and whose later ones succeed.
    @MainActor
    final class Flaky {
        var failuresLeft: Int
        init(failuresLeft: Int) { self.failuresLeft = failuresLeft }
        func fetch() throws -> String {
            if failuresLeft > 0 {
                failuresLeft -= 1
                throw HouseholdCurrencyProviderTests.Boom()
            }
            return "USD"
        }
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

    /// Review of #158: one transient failure must not leave Add disabled for
    /// the life of the tab — the error is exposed and a refresh retries.
    @Test func aFailedCurrencyFetchIsSurfacedAndRetriedByRefresh() async {
        let api = MockAccountsAPI()
        let flaky = Flaky(failuresLeft: 1)
        let provider = HouseholdCurrencyProvider(
            sessionKey: { "hh-1:device:token" }, fetch: { try flaky.fetch() })
        let viewModel = AccountsViewModel(api: api, currency: provider)

        await viewModel.loadCurrency()
        #expect(viewModel.baseCurrency == nil)
        #expect(viewModel.currencyError != nil)

        await viewModel.syncAndReload()
        #expect(viewModel.baseCurrency == "USD")
        #expect(viewModel.currencyError == nil)
    }

    /// Review of #158: names are not unique; two foreign "Savings" rows are two rows.
    @Test func foreignReservationsAreKeyedByAccountIdNotName() async {
        let api = MockAccountsAPI()
        api.currentAccounts = [
            account("eu1", "Savings", balance: money(400_000, "EUR"), reserved: money(400_000, "EUR")),
            account("eu2", "Savings", balance: money(100_000, "EUR"), reserved: money(100_000, "EUR")),
        ]
        let viewModel = AccountsViewModel(api: api, currency: provider("USD"))
        await viewModel.load()
        await viewModel.loadCurrency()

        let rows = viewModel.foreignReservations
        #expect(rows.count == 2)
        #expect(Set(rows.map(\.id)) == ["eu1", "eu2"])
        #expect(rows.map(\.name) == ["Savings", "Savings"])
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
