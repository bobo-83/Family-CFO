import Foundation

/// Everything the Bills tab does, over existing endpoints (no new endpoint):
/// recurring-bill suggestions to confirm (M58/M59), the current bills with
/// add/delete, unclassified deposits the income analysis found (M61/M63), and a
/// re-sync of linked bank connections to pull fresh transactions (M95).
protocol BillsAPI: Sendable {
    func billSuggestions() async throws -> [Components.Schemas.BillSuggestion]
    /// Confirm a suggestion by creating the real bill it describes.
    func confirmBill(_ suggestion: Components.Schemas.BillSuggestion) async throws
    func dismissBill(merchantKey: String) async throws

    /// The household's current recurring bills.
    func bills() async throws -> [Components.Schemas.Bill]
    /// The spending categories, to file bills under (M96).
    func categories() async throws -> [Components.Schemas.Category]
    /// Add a bill by hand (not from a suggestion).
    func createBill(_ request: Components.Schemas.BillCreateRequest) async throws
    func deleteBill(id: String) async throws
    /// File a bill under a category (M96). Set-only: the generated client omits a
    /// nil optional rather than sending null, so a category can't be cleared this
    /// way — un-filing is a dashboard action.
    func setBillCategory(id: String, categoryID: String) async throws

    /// Deposits the analysis couldn't classify and the user hasn't ruled on yet.
    func unclassifiedDeposits() async throws -> [Components.Schemas.IncomeAnalysisTransaction]
    func setDepositVerdict(
        transactionID: String,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async throws

    /// Re-pull transactions from every linked bank connection. Returns how many
    /// were newly imported across all connections.
    func syncAllTransactions() async throws -> Int
}

struct LiveBillsAPI: BillsAPI {
    let client: Client

    func billSuggestions() async throws -> [Components.Schemas.BillSuggestion] {
        switch try await client.listBillSuggestions(.init()) {
        case .ok(let response):
            return try response.body.json.suggestions
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func confirmBill(_ suggestion: Components.Schemas.BillSuggestion) async throws {
        let request = Components.Schemas.BillCreateRequest(
            name: suggestion.name,
            amount: suggestion.amount,
            frequency: suggestion.frequency,
            nextDueDate: suggestion.nextDueDate
        )
        switch try await client.createBill(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func dismissBill(merchantKey: String) async throws {
        let request = Components.Schemas.BillSuggestionDismissRequest(merchantKey: merchantKey)
        switch try await client.dismissBillSuggestion(.init(body: .json(request))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func bills() async throws -> [Components.Schemas.Bill] {
        switch try await client.listBills(.init()) {
        case .ok(let response):
            return try response.body.json.bills
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func createBill(_ request: Components.Schemas.BillCreateRequest) async throws {
        switch try await client.createBill(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func categories() async throws -> [Components.Schemas.Category] {
        switch try await client.listCategories(.init()) {
        case .ok(let response):
            return try response.body.json.categories
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func setBillCategory(id: String, categoryID: String) async throws {
        let request = Components.Schemas.BillUpdateRequest(categoryId: categoryID)
        switch try await client.updateBill(.init(path: .init(billId: id), body: .json(request))) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteBill(id: String) async throws {
        switch try await client.deleteBill(.init(path: .init(billId: id))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            return  // already gone
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func syncAllTransactions() async throws -> Int {
        let connections: [Components.Schemas.InstitutionConnection]
        switch try await client.listConnections(.init()) {
        case .ok(let response):
            connections = try response.body.json.connections
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }

        var imported = 0
        for connection in connections {
            switch try await client.syncConnection(.init(path: .init(connectionId: connection.id))) {
            case .ok(let response):
                imported += try response.body.json.imported
            case .unauthorized:
                throw APIError.unauthorized
            case .forbidden:
                throw APIError.server(403)
            case .notFound:
                continue  // connection removed between list and sync
            case .badGateway:
                // The bank/provider failed this connection; skip it rather than
                // abandon the whole sync, and let the imported count reflect the rest.
                continue
            case .undocumented(let status, _):
                throw APIError.server(status)
            }
        }
        return imported
    }

    func unclassifiedDeposits() async throws -> [Components.Schemas.IncomeAnalysisTransaction] {
        switch try await client.getIncomeAnalysis(.init()) {
        case .ok(let response):
            // The active review set is the deposits not yet ruled out (M63); the
            // excluded ones are the user's own past "not income" decisions.
            return try response.body.json.otherInflows
                .filter { !$0.excluded }
                .sorted { $0.occurredAt > $1.occurredAt }
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func setDepositVerdict(
        transactionID: String,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async throws {
        let request = Components.Schemas.IncomeOverrideRequest(
            transactionId: transactionID, verdict: verdict
        )
        switch try await client.setIncomeOverride(.init(body: .json(request))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
