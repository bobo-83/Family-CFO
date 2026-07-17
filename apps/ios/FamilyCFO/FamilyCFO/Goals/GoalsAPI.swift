import Foundation

/// Financial goals on iOS (M119, ADR 0025 parity with the dashboard's Goals
/// page): list with progress, create, edit (target, date, priority, planned
/// monthly contribution), delete. Every mutation is undoable (ADR 0023).
protocol GoalsAPI: Sendable {
    func goals() async throws -> [Components.Schemas.Goal]
    func createGoal(_ request: Components.Schemas.GoalCreateRequest) async throws
    func updateGoal(id: String, _ request: Components.Schemas.GoalUpdateRequest) async throws
    func deleteGoal(id: String) async throws
}

struct LiveGoalsAPI: GoalsAPI {
    let client: Client

    func goals() async throws -> [Components.Schemas.Goal] {
        switch try await client.listGoals(.init()) {
        case .ok(let response):
            return try response.body.json.goals
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func createGoal(_ request: Components.Schemas.GoalCreateRequest) async throws {
        switch try await client.createGoal(.init(body: .json(request))) {
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

    func updateGoal(id: String, _ request: Components.Schemas.GoalUpdateRequest) async throws {
        switch try await client.updateGoal(.init(path: .init(goalId: id), body: .json(request))) {
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

    func deleteGoal(id: String) async throws {
        switch try await client.deleteGoal(.init(path: .init(goalId: id))) {
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
