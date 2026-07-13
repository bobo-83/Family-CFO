import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockHouseholdAPI: HouseholdAPI, @unchecked Sendable {
    var context: Components.Schemas.HouseholdContext?
    var error: Error?
    private(set) var callCount = 0

    nonisolated func context() async throws -> Components.Schemas.HouseholdContext {
        try await MainActor.run {
            callCount += 1
            if let error { throw error }
            return context!
        }
    }
}

@MainActor
struct OverviewViewModelTests {
    private func money(_ minor: Int64) -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: "USD")
    }

    private func context() -> Components.Schemas.HouseholdContext {
        .init(
            householdId: "hh-1",
            displayName: "The Vus",
            currency: "USD",
            netWorth: money(1_234_500),
            emergencyFundMonths: 4.5
        )
    }

    @Test func loadsTheHouseholdContext() async {
        let api = MockHouseholdAPI()
        api.context = context()
        let viewModel = OverviewViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.context?.netWorth.amountMinor == 1_234_500)
        #expect(viewModel.errorMessage == nil)
        #expect(!viewModel.isLoading)
    }

    @Test func surfacesAFailureInsteadOfShowingStaleNumbers() async {
        let api = MockHouseholdAPI()
        api.error = APIError.unauthorized
        let viewModel = OverviewViewModel(api: api)

        await viewModel.load()

        #expect(viewModel.context == nil)
        #expect(viewModel.errorMessage?.contains("pairing") == true)
    }

    /// Money is stored in minor units by contract (M2); rendering them raw
    /// would show a $12,345 net worth as "$1,234,500".
    @Test func moneyFormatsFromMinorUnits() {
        #expect(money(1_234_500).formatted == "$12,345")
        #expect(money(4_299).formattedExact == "$42.99")
    }

    @Test func dueDescriptionReadsNaturally() {
        #expect(OverviewView.dueDescription(daysUntil: -1) == "Overdue")
        #expect(OverviewView.dueDescription(daysUntil: 0) == "Due today")
        #expect(OverviewView.dueDescription(daysUntil: 1) == "Due tomorrow")
        #expect(OverviewView.dueDescription(daysUntil: 5) == "Due in 5 days")
    }
}

@MainActor
struct EmergencyFundPresentationTests {
    private func fund(
        months: Double?,
        recommended: Double,
        status: Components.Schemas.EmergencyFundSummary.StatusPayload
    ) -> Components.Schemas.EmergencyFundSummary {
        .init(
            months: months,
            reserved: .init(amountMinor: 500_000, currency: "USD"),
            usingDesignations: false,
            monthlyExpenses: .init(amountMinor: 100_000, currency: "USD"),
            targetMonthsMin: 3,
            targetMonthsRecommended: recommended,
            status: status
        )
    }

    @Test func progressIsAFractionOfTheHouseholdsOwnTarget() {
        let summary = fund(months: 3, recommended: 6, status: .gettingStarted)

        #expect(summary.progressToRecommended == 0.5)
    }

    @Test func overfundedProgressClampsToFull() {
        let summary = fund(months: 12, recommended: 6, status: .fullyFunded)

        #expect(summary.progressToRecommended == 1)
    }

    /// With no bills the server can't size the fund, so there is no honest
    /// denominator — the view must show no bar rather than invent one.
    @Test func noBillsMeansNoProgressBar() {
        let summary = fund(months: nil, recommended: 6, status: .noBills)

        #expect(summary.progressToRecommended == nil)
    }
}

struct SpokenReplySentenceTests {
    @Test func splitsIntoSentencesForChunkedPlayback() {
        let chunks = SpokenReply.sentences(
            "Your net worth is up. Bills look fine! Can we afford it?")

        #expect(
            chunks == [
                "Your net worth is up.", "Bills look fine!", "Can we afford it?",
            ])
    }

    @Test func aSingleSentenceIsOneChunk() {
        #expect(SpokenReply.sentences("Just the one.") == ["Just the one."])
    }

    @Test func emptyTextYieldsNothingToSay() {
        #expect(SpokenReply.sentences("   ").isEmpty)
    }
}
