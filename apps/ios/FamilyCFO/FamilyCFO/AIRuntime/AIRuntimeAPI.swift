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
    /// #170 (ADR 0081): apply a validated selection. The server resolves the
    /// launch plan and verifies it against the installed runtime and hardware
    /// BEFORE touching the running deployment, then answers 202 with the
    /// durable job to observe. One `request_id` per user confirmation — the
    /// caller retains it across transport-ambiguous retries; a replay of the
    /// same request_id is a read, never a second launch.
    func apply(request: Components.Schemas.AiApplyRequest) async throws
        -> Components.Schemas.AiApplyJobStatus
    /// #170: read one exact apply job (nil = current/latest/idle). Read-only:
    /// never commits settings, launches recovery, or creates rows.
    func applyStatus(jobID: UUID?) async throws -> Components.Schemas.AiApplyJobStatus
    /// #170: re-run a job's frozen decision direction — a restore for
    /// `recovery_failed`, post-commit forward repair for
    /// `commit_repair_required`. Never a new model, never an image upgrade.
    /// A NEW request_id identifies the attempt.
    func retryRecovery(jobID: UUID, requestID: UUID) async throws
        -> Components.Schemas.AiApplyJobStatus
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

    func apply(request: Components.Schemas.AiApplyRequest) async throws
        -> Components.Schemas.AiApplyJobStatus
    {
        switch try await client.applyAiModelSelection(.init(body: .json(request))) {
        case .accepted(let response):
            // Durable acceptance: the plan resolved and validated; household
            // settings are NOT changed here — the reconciler commits them
            // exactly once after verified readiness (ADR 0081 S3).
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict(let response):
            // A replacement already in flight, cluster consent missing, or a
            // request_id replayed with different content — surface the
            // server's own message when it has one.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(409)
        case .unprocessableContent(let response):
            // Preflight refusal (incompatible runtime/hardware, insufficient
            // memory, …) — the safe cause the screen should show.
            if let message = try? response.body.json.error.message {
                throw APIError.advisor(message)
            }
            throw APIError.server(422)
        case .serviceUnavailable:
            throw APIError.server(503)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func applyStatus(jobID: UUID?) async throws -> Components.Schemas.AiApplyJobStatus {
        switch try await client.getAiApplyStatus(.init(query: .init(jobId: jobID?.uuidString))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .notFound:
            // Unknown retained job id — never a substituted job.
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func retryRecovery(jobID: UUID, requestID: UUID) async throws
        -> Components.Schemas.AiApplyJobStatus
    {
        switch try await client.retryAiApplyRecovery(
            .init(
                path: .init(jobId: jobID.uuidString),
                body: .json(.init(requestId: requestID.uuidString)))
        ) {
        case .accepted(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .notFound:
            throw APIError.server(404)
        case .conflict(let response):
            // Not in a recoverable state, or the recovery request_id was
            // reused with different content.
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
