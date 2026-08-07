import Foundation
import Testing

@testable import FamilyCFO

/// #10 phase 1: the Settings Language picker's view model.
@MainActor
struct HouseholdLanguageViewModelTests {
    private func context(language: String?) -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "The Vus",
            language: language,
            currency: "USD",
            netWorth: .init(amountMinor: 0, currency: "USD"),
            emergencyFundMonths: 4.5
        )
    }

    @Test func loadReadsTheHouseholdLanguage() async {
        let api = MockHouseholdAPI()
        api.context = context(language: "vi")
        let viewModel = HouseholdLanguageViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.language == "vi")
        #expect(viewModel.displayName == "Tiếng Việt")
    }

    /// A context from before the field shipped omits it — the server default.
    @Test func missingLanguageDefaultsToEnglish() async {
        let api = MockHouseholdAPI()
        api.context = context(language: nil)
        let viewModel = HouseholdLanguageViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.language == "en")
    }

    @Test func changingLanguageCallsTheAPI() async {
        let api = MockHouseholdAPI()
        api.context = context(language: "en")
        let viewModel = HouseholdLanguageViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "lt")

        #expect(api.updatedLanguages == ["lt"])
        #expect(viewModel.language == "lt")
        #expect(viewModel.errorMessage == nil)
    }

    /// Re-selecting the current language must not PATCH — the server audits
    /// every settings write, and a no-op row would be noise.
    @Test func reselectingTheCurrentLanguageDoesNothing() async {
        let api = MockHouseholdAPI()
        api.context = context(language: "vi")
        let viewModel = HouseholdLanguageViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "vi")

        #expect(api.updatedLanguages.isEmpty)
    }

    @Test func failureRevertsThePickerAndSurfacesTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(language: "en")
        api.mutationError = APIError.server(422)
        let viewModel = HouseholdLanguageViewModel(api: api)
        await viewModel.load()

        await viewModel.change(to: "vi")

        #expect(viewModel.language == "en")
        #expect(viewModel.errorMessage != nil)
    }

    /// A later success clears the stale failure message.
    @Test func successAfterAFailureClearsTheError() async {
        let api = MockHouseholdAPI()
        api.context = context(language: "en")
        api.mutationError = APIError.server(422)
        let viewModel = HouseholdLanguageViewModel(api: api)
        await viewModel.load()
        await viewModel.change(to: "vi")

        api.mutationError = nil
        await viewModel.change(to: "lt")

        #expect(viewModel.language == "lt")
        #expect(viewModel.errorMessage == nil)
    }
}
