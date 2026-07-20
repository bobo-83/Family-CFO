import Foundation
import Observation

/// Drives the Income tab: the analyzed income picture (M73) plus earner
/// management. The W-2 scan lives here too — income surfaces live on the
/// Income page, the same way loans live on Debts.
@MainActor
@Observable
final class IncomeViewModel {
    let api: IncomeAPI

    private(set) var analysis: Components.Schemas.IncomeAnalysisResponse?
    private(set) var isLoading = false
    private(set) var deletingID: String?
    var errorMessage: String?

    init(api: IncomeAPI) { self.api = api }

    var earners: [Components.Schemas.IncomeEarner] {
        analysis?.profile?.earners ?? []
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            analysis = try await api.analysis()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func deleteEarner(_ earner: Components.Schemas.IncomeEarner) async {
        guard deletingID == nil else { return }
        deletingID = earner.id
        defer { deletingID = nil }
        do {
            try await api.deleteEarner(id: earner.id)
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
