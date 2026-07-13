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
    /// Create a category inline while categorizing, and return it (M91a). Full
    /// category management still lives on the dashboard; this is just the
    /// on-ramp so the phone isn't a dead end with no categories defined.
    func createCategory(name: String) async throws -> Components.Schemas.Category
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

    func createCategory(name: String) async throws -> Components.Schemas.Category {
        let request = Components.Schemas.CategoryCreateRequest(name: name)
        switch try await client.createCategory(.init(body: .json(request))) {
        case .created(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict:
            // A category by that name already exists — surface it plainly so the
            // user knows to pick it rather than think the tap failed.
            throw CategorizeError.categoryExists(name)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}

enum CategorizeError: Error, LocalizedError, Equatable {
    case categoryExists(String)

    var errorDescription: String? {
        switch self {
        case .categoryExists(let name):
            return "A category named “\(name)” already exists — pick it from the list."
        }
    }
}
