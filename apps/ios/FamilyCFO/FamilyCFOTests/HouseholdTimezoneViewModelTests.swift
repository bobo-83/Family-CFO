import Foundation
import Testing

@testable import FamilyCFO

/// #41: the Settings Time zone row's view model.
@MainActor
struct HouseholdTimezoneViewModelTests {
    private func context(timezone: String?) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "The Vus",
            timezone: timezone,
            currency: "USD",
            netWorth: .init(amountMinor: 0, currency: "USD"),
            emergencyFundMonths: 4.5
        )
    }

    @Test func loadReadsTheHouseholdZone() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "Europe/London")
        let viewModel = HouseholdTimezoneViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.timezone == "Europe/London")
        #expect(viewModel.displayName == "Europe/London")
    }

    /// A household that never chose one: dates follow the box's own zone, and
    /// the row has to say so rather than showing a blank.
    @Test func noZoneSaysSoRatherThanShowingABlank() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: nil)
        let viewModel = HouseholdTimezoneViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.timezone == nil)
        #expect(!viewModel.displayName.isEmpty)
    }

    @Test func changingTheZoneCallsTheAPI() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "America/New_York")
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "Europe/London")

        #expect(api.updatedTimezones == ["Europe/London"])
        #expect(viewModel.timezone == "Europe/London")
        #expect(viewModel.errorMessage == nil)
    }

    /// Re-picking the zone already set must not PATCH — the server audits every
    /// settings write, and a no-op row would be noise.
    @Test func repickingTheCurrentZoneDoesNothing() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "Europe/London")
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "Europe/London")

        #expect(api.updatedTimezones.isEmpty)
    }

    /// The server 422s a zone it doesn't know rather than shifting every date
    /// by hours — the row has to go back to the zone that is really stored.
    @Test func anUnknownZoneRevertsTheRowAndSurfacesTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "America/New_York")
        api.mutationError = APIError.server(422)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "Mars/Olympus_Mons")

        #expect(viewModel.timezone == "America/New_York")
        #expect(viewModel.errorMessage != nil)
    }

    @Test func successAfterAFailureClearsTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "America/New_York")
        api.mutationError = APIError.server(422)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()
        await viewModel.change(to: "Mars/Olympus_Mons")

        api.mutationError = nil
        await viewModel.change(to: "Europe/London")

        #expect(viewModel.timezone == "Europe/London")
        #expect(viewModel.errorMessage == nil)
    }

    // --- the list itself: hundreds of zones must not be a wall ---------------

    @Test func theShortlistIsScannableAndReachesTheCommonZones() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: nil)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        let shortlist = viewModel.options(matching: "")

        #expect(shortlist.contains("Europe/London"))
        #expect(shortlist.contains("America/New_York"))
        #expect(shortlist.count < 25)
        #expect(Set(shortlist).count == shortlist.count)  // no duplicates
    }

    /// A zone outside the curated set would otherwise look unselected.
    @Test func theCurrentZoneLeadsTheShortlistEvenWhenItIsNotCurated() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "Pacific/Chatham")
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        #expect(viewModel.options(matching: "").first == "Pacific/Chatham")
    }

    @Test func searchReachesEveryZoneAndTreatsSpacesAsUnderscores() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: nil)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        #expect(viewModel.options(matching: "new york").contains("America/New_York"))
        #expect(viewModel.options(matching: "chatham").contains("Pacific/Chatham"))
        // Curated entries the device's own list may omit stay findable.
        #expect(viewModel.options(matching: "UTC").contains("UTC"))
    }
}

/// #43: getting back to "follow the box's own zone" after a zone was picked.
@MainActor
struct HouseholdTimezoneClearTests {
    private func context(timezone: String?) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "The Vus",
            timezone: timezone,
            currency: "USD",
            netWorth: .init(amountMinor: 0, currency: "USD"),
            emergencyFundMonths: 4.5
        )
    }

    @Test func clearingSendsNilAndDropsBackToTheBoxsZone() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "Europe/London")
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: nil)

        #expect(api.updatedTimezones == [String?.none])
        #expect(viewModel.timezone == nil)
        #expect(viewModel.errorMessage == nil)
    }

    /// The row is only worth offering when there is something to clear.
    @Test func theBoxsZoneRowAppearsOnlyOnceAZoneIsSet() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: nil)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        #expect(!viewModel.offersBoxDefault(matching: ""))

        await viewModel.change(to: "Europe/London")

        #expect(viewModel.offersBoxDefault(matching: ""))
        // A row that names no zone would read as noise among search hits.
        #expect(!viewModel.offersBoxDefault(matching: "london"))
    }

    /// Already on the box's zone: re-picking must not PATCH, exactly like
    /// re-picking the zone already set — the server audits every write.
    @Test func clearingAnAlreadyClearedZoneDoesNothing() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: nil)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: nil)

        #expect(api.updatedTimezones.isEmpty)
    }

    @Test func aRejectedClearRevertsTheRowAndSurfacesTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(timezone: "Europe/London")
        api.mutationError = APIError.server(422)
        let viewModel = HouseholdTimezoneViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: nil)

        #expect(viewModel.timezone == "Europe/London")
        #expect(viewModel.errorMessage != nil)
    }
}
