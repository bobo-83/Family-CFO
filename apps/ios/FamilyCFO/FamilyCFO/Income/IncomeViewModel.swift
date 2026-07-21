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
    private(set) var categories: [Components.Schemas.Category] = []
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
            async let analysisResult = api.analysis()
            async let categoriesResult = api.categories()
            analysis = try await analysisResult
            categories = (try? await categoriesResult) ?? categories
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// ADR 0055: reclassify a deposit from the income page — e.g. a transfer of
    /// already-counted RSU proceeds that was double-counted as income. Moving it
    /// off the Income category drops it from the rollup.
    func recategorize(
        _ txn: Components.Schemas.IncomeAnalysisTransaction, to categoryID: String
    ) async {
        do {
            try await api.setCategory(transactionID: txn.transactionId, categoryID: categoryID)
            errorMessage = nil
            await load()
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
