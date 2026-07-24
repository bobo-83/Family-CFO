import Foundation
import Observation

/// Drives the Overview's Year mode (M-yearly): the monthly trend chart, year
/// totals, top categories, and the grounded review with its regenerate flow.
@MainActor
@Observable
final class YearlyOverviewViewModel {
    private let api: HouseholdAPI

    private(set) var overview: Components.Schemas.YearlyOverview?
    private(set) var isLoading = false
    private(set) var isGenerating = false
    var errorMessage: String?

    init(api: HouseholdAPI) { self.api = api }

    var year: Int { overview?.year ?? Calendar.current.component(.year, from: .now) }

    func load(year: Int? = nil) async {
        isLoading = true
        defer { isLoading = false }
        do {
            overview = try await api.yearly(year: year)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Step to an adjacent year and reload.
    func step(_ delta: Int) async {
        await load(year: year + delta)
    }

    /// (Re)generate the narrative on the box — a model round, takes seconds.
    func generateReview() async {
        guard !isGenerating else { return }
        isGenerating = true
        defer { isGenerating = false }
        do {
            let review = try await api.generateYearlyReview(year: year)
            overview?.review = review
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
