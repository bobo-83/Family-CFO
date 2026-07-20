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
    /// The payment timeline (M111, ADR 0024): bills, card payments, and loan/lease
    /// payments as one time-ordered list with a cash-versus-due headline.
    func paymentTimeline() async throws -> Components.Schemas.PaymentTimelineResponse?
    /// Recurring obligations that live on liability accounts — loans, leases,
    /// payroll-deducted 401(k) loans (M106). Shown on Bills so every recurring
    /// commitment is in one place.
    func obligations() async throws -> [Components.Schemas.AccountObligation]
    /// The spending categories, to file bills under (M96).
    func categories() async throws -> [Components.Schemas.Category]
    /// Add a bill by hand (not from a suggestion).
    func createBill(_ request: Components.Schemas.BillCreateRequest) async throws
    /// Read a photographed/uploaded bill into candidate values that prefill the
    /// add-bill form — the user confirms before anything is saved.
    func scanBill(_ attachment: ChatAttachment) async throws -> Components.Schemas.BillScanResult
    /// Edit an existing bill's core fields (name, amount, frequency, next-due date,
    /// and — set-only — its category). Backed by the same `updateBill` endpoint as
    /// `setBillCategory`; the server records it as an undoable action.
    func updateBill(
        id: String,
        name: String,
        amountMinor: Int64,
        currency: String,
        frequency: Components.Schemas.RecurringFrequency,
        nextDueDate: String?,
        categoryID: String?
    ) async throws
    func deleteBill(id: String) async throws
    /// File a bill under a category (M96); returns how many matching transactions
    /// were also auto-filed (the M96 propagation rule). Set-only: the generated
    /// client omits a nil optional rather than sending null, so a category can't be
    /// cleared this way — un-filing is a dashboard action.
    func setBillCategory(id: String, categoryID: String) async throws -> Int

    /// Deposits the analysis couldn't classify and the user hasn't ruled on yet.
    func unclassifiedDeposits() async throws -> [Components.Schemas.IncomeAnalysisTransaction]
    func setDepositVerdict(
        transactionID: String,
        verdict: Components.Schemas.IncomeOverrideRequest.VerdictPayload
    ) async throws

    /// Re-pull transactions from every linked bank connection, returning the
    /// totals across all connections.
    func syncAllTransactions() async throws -> SyncTotals
    /// Delete a category so the shared picker's long-press delete works here too.
    func deleteCategory(id: String) async throws
}

extension BillsAPI {
    /// Defaults so mocks/tests needn't implement them; the live client overrides.
    func obligations() async throws -> [Components.Schemas.AccountObligation] { [] }
    func deleteCategory(id: String) async throws {}
    func paymentTimeline() async throws -> Components.Schemas.PaymentTimelineResponse? { nil }
    func scanBill(_ attachment: ChatAttachment) async throws -> Components.Schemas.BillScanResult {
        Components.Schemas.BillScanResult(note: "Scanning is not available.")
    }
    func updateBill(
        id: String,
        name: String,
        amountMinor: Int64,
        currency: String,
        frequency: Components.Schemas.RecurringFrequency,
        nextDueDate: String?,
        categoryID: String?
    ) async throws {}
}

/// Aggregate outcome of syncing every connection (M96): what was newly imported,
/// and how much the system filed on the user's behalf so they need not tag it.
struct SyncTotals: Equatable {
    var imported = 0
    var transfersFiled = 0
    var autoCategorized = 0
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

    func obligations() async throws -> [Components.Schemas.AccountObligation] {
        switch try await client.listBills(.init()) {
        case .ok(let response):
            return try response.body.json.accountObligations ?? []
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func paymentTimeline() async throws -> Components.Schemas.PaymentTimelineResponse? {
        switch try await client.getPaymentTimeline(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteCategory(id: String) async throws {
        switch try await client.deleteCategory(.init(path: .init(categoryId: id))) {
        case .noContent, .notFound: return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
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

    func scanBill(_ attachment: ChatAttachment) async throws -> Components.Schemas.BillScanResult {
        guard case .visual(let mediaType) = attachment.kind,
            let scanMediaType = Components.Schemas.BillScanRequest.ImageMediaTypePayload(
                rawValue: mediaType.rawValue)
        else {
            throw APIError.server(415)
        }
        let request = Components.Schemas.BillScanRequest(
            imageBase64: attachment.data.base64EncodedString(),
            imageMediaType: scanMediaType
        )
        switch try await client.scanBill(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .unprocessableContent:
            throw APIError.server(422)
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateBill(
        id: String,
        name: String,
        amountMinor: Int64,
        currency: String,
        frequency: Components.Schemas.RecurringFrequency,
        nextDueDate: String?,
        categoryID: String?
    ) async throws {
        // The generated client omits a nil `categoryId` rather than sending null,
        // so passing nil leaves the existing category untouched (clearing a
        // category is a dashboard action, same constraint as setBillCategory).
        let request = Components.Schemas.BillUpdateRequest(
            name: name,
            amount: .init(amountMinor: amountMinor, currency: currency),
            frequency: frequency,
            nextDueDate: nextDueDate,
            categoryId: categoryID)
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

    func setBillCategory(id: String, categoryID: String) async throws -> Int {
        let request = Components.Schemas.BillUpdateRequest(categoryId: categoryID)
        switch try await client.updateBill(.init(path: .init(billId: id), body: .json(request))) {
        case .ok(let response):
            return try response.body.json.transactionsCategorized ?? 0
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

    func syncAllTransactions() async throws -> SyncTotals {
        switch try await client.syncAllConnections(.init()) {
        case .ok(let response):
            let r = try response.body.json
            return SyncTotals(
                imported: r.imported,
                transfersFiled: r.transfersFiled ?? 0,
                autoCategorized: r.autoCategorized ?? 0
            )
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
