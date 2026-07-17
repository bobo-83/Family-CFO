import Foundation

/// Monthly per-category envelopes (M46), on iOS at last (M118, ADR 0025 parity
/// with the dashboard's Budgets page): list with current-month progress, create,
/// change a limit, delete. Every mutation is undoable server-side (ADR 0023).
protocol BudgetsAPI: Sendable {
    func budgets() async throws -> [Components.Schemas.Budget]
    func categories() async throws -> [Components.Schemas.Category]
    func createBudget(categoryID: String, limitMinor: Int64, currency: String) async throws
    func updateBudget(id: String, limitMinor: Int64, currency: String) async throws
    func deleteBudget(id: String) async throws
}

struct LiveBudgetsAPI: BudgetsAPI {
    let client: Client

    func budgets() async throws -> [Components.Schemas.Budget] {
        switch try await client.listBudgets(.init()) {
        case .ok(let response):
            return try response.body.json.budgets
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

    func createBudget(categoryID: String, limitMinor: Int64, currency: String) async throws {
        let request = Components.Schemas.BudgetCreateRequest(
            categoryId: categoryID,
            limit: .init(amountMinor: limitMinor, currency: currency))
        switch try await client.createBudget(.init(body: .json(request))) {
        case .created:
            return
        case .badRequest:
            throw APIError.server(400)
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict:
            throw APIError.server(409)
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateBudget(id: String, limitMinor: Int64, currency: String) async throws {
        let request = Components.Schemas.BudgetUpdateRequest(
            limit: .init(amountMinor: limitMinor, currency: currency))
        switch try await client.updateBudget(.init(path: .init(budgetId: id), body: .json(request))) {
        case .ok:
            return
        case .badRequest:
            throw APIError.server(400)
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

    func deleteBudget(id: String) async throws {
        switch try await client.deleteBudget(.init(path: .init(budgetId: id))) {
        case .noContent, .notFound:
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
