import Foundation

/// AI runtime management on the phone (ADR 0025 parity — this closes the
/// "manage the AI runtime on the web dashboard" exception): see what the box
/// is running, browse/search models with hardware-fit context, and apply a
/// swap with live progress.
protocol AIRuntimeAPI: Sendable {
    func status() async throws -> Components.Schemas.AiRuntimeStatus
    func catalog() async throws -> [Components.Schemas.AiModelInfo]
    func hardware() async throws -> Components.Schemas.AiHardwareProfile
    /// ADR 0071: the raw runtime config — carries the cluster toggle.
    func runtimeConfig() async throws -> Components.Schemas.AiRuntimeConfig
    /// Full-object PUT; the caller preserves provider/base_url/model/enabled.
    func updateRuntimeConfig(_ config: Components.Schemas.AiRuntimeConfig) async throws
        -> Components.Schemas.AiRuntimeConfig
    func search(query: String) async throws -> [Components.Schemas.AiModelInfo]
    /// Drill-down: catalog/estimated specs + the hub's live stats for one model.
    func detail(id: String) async throws -> Components.Schemas.AiModelDetail
    /// Kick off a swap: download (if needed) and restart the runtime.
    /// `cluster` (ADR 0071) asks for tensor-parallel serving across the paired
    /// second box; the server answers 409 when the peer is unreachable.
    func apply(mainModel: String, visionModel: String?, cluster: Bool) async throws
        -> Components.Schemas.AiSwapStatus
    func applyStatus() async throws -> Components.Schemas.AiSwapStatus
    /// #181: per-household usage for the operator's fairness view. Requires
    /// the AI-runtime-manage right — the server answers 403 otherwise.
    func usage() async throws -> Components.Schemas.AiUsageResponse
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

    func runtimeConfig() async throws -> Components.Schemas.AiRuntimeConfig {
        switch try await client.getAiRuntimeConfig(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func updateRuntimeConfig(_ config: Components.Schemas.AiRuntimeConfig) async throws
        -> Components.Schemas.AiRuntimeConfig
    {
        switch try await client.updateAiRuntimeConfig(.init(body: .json(config))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func detail(id: String) async throws -> Components.Schemas.AiModelDetail {
        switch try await client.getAiModelDetail(.init(query: .init(id: id))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            throw APIError.server(404)
        case .serviceUnavailable:
            throw APIError.server(503)
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

    func apply(mainModel: String, visionModel: String?, cluster: Bool) async throws
        -> Components.Schemas.AiSwapStatus
    {
        switch try await client.applyAiModelSelection(
            .init(body: .json(.init(
                mainModel: mainModel,
                // Single-box applies omit the flag (same as cluster: false).
                cluster: cluster ? true : nil,
                visionModel: visionModel)))
        ) {
        case .accepted(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict(let response):
            // A swap already running, or (ADR 0071) a cluster apply with the
            // peer down — surface the server's own message when it has one.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
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

    func usage() async throws -> Components.Schemas.AiUsageResponse {
        switch try await client.getAiUsage(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
