import Foundation

/// AI runtime management on the phone (ADR 0025 parity — this closes the
/// "manage the AI runtime on the web dashboard" exception): see what the box
/// is running, browse/search models with hardware-fit context, and apply a
/// swap with live progress.
protocol AIRuntimeAPI: Sendable {
    func status() async throws -> Components.Schemas.AiRuntimeStatus
    func catalog() async throws -> [Components.Schemas.AiModelInfo]
    func hardware() async throws -> Components.Schemas.AiHardwareProfile
    func search(query: String) async throws -> [Components.Schemas.AiModelInfo]
    /// Kick off a swap: download (if needed) and restart the runtime.
    func apply(mainModel: String, visionModel: String?) async throws
        -> Components.Schemas.AiSwapStatus
    func applyStatus() async throws -> Components.Schemas.AiSwapStatus
}

struct LiveAIRuntimeAPI: AIRuntimeAPI {
    let client: Client

    func status() async throws -> Components.Schemas.AiRuntimeStatus {
        switch try await client.getAiRuntimeStatus(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func catalog() async throws -> [Components.Schemas.AiModelInfo] {
        switch try await client.listAiModels(.init()) {
        case .ok(let response):
            return try response.body.json.models
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func hardware() async throws -> Components.Schemas.AiHardwareProfile {
        switch try await client.getAiHardwareProfile(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func search(query: String) async throws -> [Components.Schemas.AiModelInfo] {
        // limit 30 (the contract maximum) — the default of 10 per pipeline,
        // sorted by downloads, hid most variants of a searched family
        // (user report 2026-07-22: "I don't see all of Qwen3.6").
        switch try await client.searchAiModels(.init(query: .init(q: query, limit: 30))) {
        case .ok(let response):
            return try response.body.json.models
        case .unauthorized:
            throw APIError.unauthorized
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func apply(mainModel: String, visionModel: String?) async throws
        -> Components.Schemas.AiSwapStatus
    {
        switch try await client.applyAiModelSelection(
            .init(body: .json(.init(mainModel: mainModel, visionModel: visionModel)))
        ) {
        case .accepted(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict:
            // A swap is already running — the status poll will pick it up.
            throw APIError.server(409)
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func applyStatus() async throws -> Components.Schemas.AiSwapStatus {
        switch try await client.getAiApplyStatus(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
