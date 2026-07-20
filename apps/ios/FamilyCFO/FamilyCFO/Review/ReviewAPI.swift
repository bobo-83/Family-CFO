import Foundation

/// `Transaction` carries a stable `id`; conforming lets it drive `.sheet(item:)`.
extension Components.Schemas.Transaction: Identifiable {}

/// The duplicate-review queue (M97): exact-duplicate charges the bank reported
/// twice (same account, date, amount, merchant) with different provider ids. The
/// user keeps them (a real repeat), disputes them, or deletes an erroneous one.
/// What the Review tab can review.
enum ReviewKind: String {
    case duplicates, transfers, credits
    /// ADR 0049: sizeable inflows filed as a Transfer with no matching internal
    /// leg — likely misfiled paychecks the user confirms as income (or keeps).
    case suspectedIncome = "suspected_income"
}

protocol ReviewAPI: Sendable {
    /// Transactions to review of a given kind: possible duplicates, transfers, or
    /// credits/refunds.
    func queue(kind: ReviewKind) async throws -> [Components.Schemas.Transaction]
    /// Keep a group (real repeat) — 'dismissed' — or mark it 'disputed'.
    func setState(
        transactionID: String,
        state: Components.Schemas.TransactionUpdateRequest.DuplicateStatePayload
    ) async throws
    /// Remove an erroneous duplicate outright.
    func delete(transactionID: String) async throws
    /// Recategorize a transaction (from the transfers/credits review lists). A nil
    /// categoryID clears it — used to undo back to uncategorized.
    func setCategory(transactionID: String, categoryID: String?) async throws
    /// ADR 0049: "keep as transfer" — record an income-override 'exclude' so this
    /// suspected-income candidate is never flagged again.
    func keepAsTransfer(transactionID: String) async throws
    /// Create a category (used to file a confirmed paycheck under Income when the
    /// household has no Income category yet). Returns the existing one on conflict.
    func createCategory(name: String) async throws -> Components.Schemas.Category
    func categories() async throws -> [Components.Schemas.Category]
    /// Delete a category so the shared picker's long-press delete works here too.
    func deleteCategory(id: String) async throws
    /// Pull fresh transactions from the linked banks so pull-to-refresh surfaces
    /// newly-flagged items, matching every other synced tab (M103).
    func syncBanks() async throws
}

extension ReviewAPI {
    /// Defaults so mocks/tests needn't implement them; the live client overrides.
    func syncBanks() async throws {}
    func deleteCategory(id: String) async throws {}
}

struct LiveReviewAPI: ReviewAPI {
    let client: Client

    func syncBanks() async throws {
        switch try await client.syncAllConnections(.init()) {
        case .ok: return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func queue(kind: ReviewKind) async throws -> [Components.Schemas.Transaction] {
        let payload = Operations.ListTransactionsForReview.Input.Query.KindPayload(
            rawValue: kind.rawValue)
        switch try await client.listTransactionsForReview(.init(query: .init(kind: payload))) {
        case .ok(let response):
            return try response.body.json.transactions
        case .unauthorized:
            throw APIError.unauthorized
        case .unprocessableContent:
            throw APIError.server(422)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func setCategory(transactionID: String, categoryID: String?) async throws {
        let request = Components.Schemas.TransactionUpdateRequest(
            categoryId: categoryID, clearCategory: categoryID == nil ? true : nil)
        switch try await client.updateTransaction(
            .init(path: .init(transactionId: transactionID), body: .json(request))
        ) {
        case .ok, .notFound:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func keepAsTransfer(transactionID: String) async throws {
        let request = Components.Schemas.IncomeOverrideRequest(
            transactionId: transactionID, verdict: .exclude)
        switch try await client.setIncomeOverride(.init(body: .json(request))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
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
        case .conflict:
            // Already exists — return it so the caller can file under it.
            let existing = try await categories().first {
                $0.name.lowercased() == name.lowercased()
            }
            guard let existing else { throw APIError.server(409) }
            return existing
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

    func deleteCategory(id: String) async throws {
        switch try await client.deleteCategory(.init(path: .init(categoryId: id))) {
        case .noContent, .notFound: return
        case .unauthorized: throw APIError.unauthorized
        case .forbidden: throw APIError.server(403)
        case .undocumented(let status, _): throw APIError.server(status)
        }
    }

    func setState(
        transactionID: String,
        state: Components.Schemas.TransactionUpdateRequest.DuplicateStatePayload
    ) async throws {
        let request = Components.Schemas.TransactionUpdateRequest(duplicateState: state)
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
            // Already gone; the queue will simply no longer show it.
            return
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func delete(transactionID: String) async throws {
        switch try await client.deleteTransaction(.init(path: .init(transactionId: transactionID))) {
        case .noContent:
            return
        case .notFound:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
