import Foundation

/// Quick transaction categorization (M91), over the existing transaction and
/// category endpoints. "Uncategorized" is `category_id == nil`: the contract has
/// no review-status field and the list endpoint takes no filter, so the phone
/// pulls the transactions and picks out the ones with no category.
protocol CategorizeAPI: Sendable {
    func uncategorized() async throws -> [Components.Schemas.Transaction]
    func categories() async throws -> [Components.Schemas.Category]
    /// Assign a category. `nil` clears it (the undo path).
    func setCategory(transactionID: String, categoryID: String?) async throws
}

struct LiveCategorizeAPI: CategorizeAPI {
    let client: Client

    func uncategorized() async throws -> [Components.Schemas.Transaction] {
        switch try await client.listTransactions(.init()) {
        case .ok(let response):
            return try response.body.json.transactions
                .filter { $0.categoryId == nil }
                // Newest first — the freshest spending is what you remember.
                .sorted { $0.occurredAt > $1.occurredAt }
        case .unauthorized:
            throw APIError.unauthorized
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

    func setCategory(transactionID: String, categoryID: String?) async throws {
        // clear_category and category_id are mutually exclusive on the contract:
        // send exactly one, depending on whether we're assigning or undoing.
        let request = Components.Schemas.TransactionUpdateRequest(
            categoryId: categoryID,
            clearCategory: categoryID == nil ? true : nil
        )
        switch try await client.updateTransaction(
            .init(path: .init(transactionId: transactionID), body: .json(request))
        ) {
        case .ok:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            // Already gone (deleted elsewhere); the goal — not uncategorized —
            // is achieved either way.
            return
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
