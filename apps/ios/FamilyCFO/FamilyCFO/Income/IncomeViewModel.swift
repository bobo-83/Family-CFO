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
    /// M-rsu-grants: grants with vest schedules, live quotes, derived annual.
    private(set) var rsuGrants: Components.Schemas.RsuGrantsResponse?
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
            async let rsuResult = api.rsuGrants()
            analysis = try await analysisResult
            categories = (try? await categoriesResult) ?? categories
            rsuGrants = (try? await rsuResult) ?? rsuGrants
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

    // MARK: RSU grants (M-rsu-grants)

    func addGrant(
        earnerID: String,
        ticker: String,
        units: Int,
        grantDate: String,
        vestYears: Int,
        frequency: Components.Schemas.RsuGrantCreateRequest.FrequencyPayload
    ) async {
        await mutateRsu {
            try await self.api.createRsuGrant(
                .init(
                    earnerId: earnerID,
                    ticker: ticker,
                    units: units,
                    grantDate: grantDate,
                    vestYears: vestYears,
                    frequency: frequency
                ))
        }
    }

    func deleteGrant(_ grant: Components.Schemas.RsuGrant) async {
        await mutateRsu { try await self.api.deleteRsuGrant(id: grant.id) }
    }

    func addVestEvent(grantID: String, date: String, units: Int) async {
        await mutateRsu {
            try await self.api.addRsuVestEvent(grantID: grantID, date: date, units: units)
        }
    }

    func updateVestEvent(id: String, date: String?, units: Int?) async {
        await mutateRsu {
            try await self.api.updateRsuVestEvent(id: id, date: date, units: units)
        }
    }

    func deleteVestEvent(id: String) async {
        await mutateRsu { try await self.api.deleteRsuVestEvent(id: id) }
    }

    /// Re-fetch the live quote; the endpoint returns the refreshed picture, so
    /// no full reload is needed.
    func refreshQuote() async {
        do {
            if let refreshed = try await api.refreshRsuQuotes() {
                rsuGrants = refreshed
            }
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// One RSU mutation shape: call the API, then reload the whole picture —
    /// grants feed the derived annual, which feeds the income rollup.
    private func mutateRsu(_ operation: () async throws -> Void) async {
        do {
            try await operation()
            errorMessage = nil
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
