import Foundation
import Testing

@testable import FamilyCFO

/// #5: the Settings "Reserve committed savings" toggle's view model.
@MainActor
struct CommittedSavingsReserveViewModelTests {
    private func context(reserved: Bool?) -> Components.Schemas.HouseholdContext {
        var sts: Components.Schemas.SafeToSpend?
        if let reserved {
            sts = .init(
                liquidBalance: .init(amountMinor: 100_000, currency: "USD"),
                emergencyFundReserved: .init(amountMinor: 0, currency: "USD"),
                billsDue: .init(amountMinor: 0, currency: "USD"),
                minimumDebtPayments: .init(amountMinor: 0, currency: "USD"),
                committedTotal: .init(amountMinor: 0, currency: "USD"),
                safeToSpend: .init(amountMinor: 100_000, currency: "USD"),
                totalDebt: .init(amountMinor: 0, currency: "USD"),
                warnings: [],
                committedSavings: .init(value1: .init(amountMinor: 50_000, currency: "USD")),
                committedSavingsReserved: reserved)
        }
        return .init(
            householdId: "hh-1",
            displayName: "demo-household",
            currency: "USD",
            netWorth: .init(amountMinor: 0, currency: "USD"),
            emergencyFundMonths: 4.5,
            safeToSpend: sts)
    }

    @Test func loadReadsTheReservedFlag() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: true)
        let viewModel = CommittedSavingsReserveViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.reserved == true)
    }

    /// No committed savings in the horizon → nothing reported → the default.
    @Test func absentSafeToSpendDefaultsToOff() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: nil)
        let viewModel = CommittedSavingsReserveViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.reserved == false)
    }

    @Test func togglingOnPostsAndRefreshes() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: true)  // server has applied it on refresh
        let viewModel = CommittedSavingsReserveViewModel(api: api)
        await viewModel.load()
        #expect(viewModel.reserved == true)
        // Start from off so the flip is a real change.
        api.context = context(reserved: false)
        await viewModel.load()
        let callsBefore = api.callCount

        api.context = context(reserved: true)
        await viewModel.setReserved(true)

        #expect(api.reserveUpdates == [true])
        #expect(viewModel.reserved == true)
        #expect(viewModel.errorMessage == nil)
        // The refresh re-read the context after the successful PATCH.
        #expect(api.callCount == callsBefore + 1)
    }

    /// Re-selecting the current value must not PATCH — the server audits writes.
    @Test func settingTheCurrentValueDoesNothing() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: true)
        let viewModel = CommittedSavingsReserveViewModel(api: api)
        await viewModel.load()

        await viewModel.setReserved(true)

        #expect(api.reserveUpdates.isEmpty)
    }

    @Test func failureRevertsTheToggleAndSurfacesTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: false)
        api.mutationError = APIError.server(403)
        let viewModel = CommittedSavingsReserveViewModel(api: api)
        await viewModel.load()

        await viewModel.setReserved(true)

        #expect(viewModel.reserved == false)
        #expect(viewModel.errorMessage != nil)
    }

    /// A later success clears the stale failure message.
    @Test func successAfterAFailureClearsTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(reserved: false)
        api.mutationError = APIError.server(403)
        let viewModel = CommittedSavingsReserveViewModel(api: api)
        await viewModel.load()
        await viewModel.setReserved(true)

        api.mutationError = nil
        api.context = context(reserved: true)
        await viewModel.setReserved(true)

        #expect(viewModel.reserved == true)
        #expect(viewModel.errorMessage == nil)
    }
}
