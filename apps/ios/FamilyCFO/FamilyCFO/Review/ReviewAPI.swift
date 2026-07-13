import Foundation

/// The review queues (M90): two kinds of one-tap decisions, over existing
/// endpoints — recurring-bill suggestions (M58/M59) and unclassified deposits
/// the income analysis found (M61/M63). Read + act only; no new endpoint.
protocol ReviewAPI: Sendable {
    func billSuggestions() async throws -> [Components.Schemas.BillSuggestion]
    /// Confirm a suggestion by creating the real bill it describes.
    func confirmBill(_ suggestion: Components.Schemas.BillSuggestion) async throws
    func dismissBill(merchantKey: String) async throws

    /// Deposits the analysis couldn't classify and the user hasn't ruled on yet.
    func unclassifiedDeposits() async throws -> [Components.Schemas.IncomeAnalysisTransaction]
    func setDepositVerdict(
        transactionID: String,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async throws
}

struct LiveReviewAPI: ReviewAPI {
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
