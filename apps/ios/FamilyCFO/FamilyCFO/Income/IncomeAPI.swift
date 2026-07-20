import Foundation

/// The W2-scan on-ramp to adding an income earner (M89, over the M73/M76
/// endpoints). Two calls, deliberately separate: the scan returns CANDIDATE
/// values and saving is a second, explicit act — a model never writes financial
/// ground truth directly (M73).
protocol IncomeAPI: Sendable {
    func scanW2(_ attachment: ChatAttachment) async throws -> Components.Schemas.W2ScanResult
    func createEarner(_ request: Components.Schemas.IncomeEarnerCreateRequest) async throws
    /// The full income picture (M73): detected sources, rollup, earners, tax.
    func analysis() async throws -> Components.Schemas.IncomeAnalysisResponse
    func deleteEarner(id: String) async throws
}

enum IncomeAPIError: Error, LocalizedError, Equatable {
    case unsupportedScanFile
    case forbidden
    case unreadableScan
    case visionModelUnavailable

    var errorDescription: String? {
        switch self {
        case .unsupportedScanFile:
            return "That file can't be scanned — use a photo or a PDF of the W-2."
        case .forbidden:
            return "Only an owner or adult can add an income earner."
        case .unreadableScan:
            return "Couldn't read a W-2 from that image. Try a straighter, better-lit photo of the whole form."
        case .visionModelUnavailable:
            return "The box's vision model isn't running, so it can't read the form. You can still type the figures in below."
        }
    }
}

struct LiveIncomeAPI: IncomeAPI {
    let client: Client

    func scanW2(_ attachment: ChatAttachment) async throws -> Components.Schemas.W2ScanResult {
        // The scan endpoint has its own media-type enum. Same raw values as the
        // chat one, but a distinct type — bridge by raw value rather than
        // assuming they stay identical.
        guard case .visual(let mediaType) = attachment.kind,
            let scanMediaType = Components.Schemas.W2ScanRequest.ImageMediaTypePayload(
                rawValue: mediaType.rawValue)
        else {
            throw IncomeAPIError.unsupportedScanFile
        }
        let request = Components.Schemas.W2ScanRequest(
            imageBase64: attachment.data.base64EncodedString(),
            imageMediaType: scanMediaType
        )
        switch try await client.scanW2(.init(body: .json(request))) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw IncomeAPIError.forbidden
        case .unprocessableContent:
            throw IncomeAPIError.unreadableScan
        case .serviceUnavailable:
            // The vision model is optional infrastructure; a failed scan must
            // still leave the user able to type the figures in by hand.
            throw IncomeAPIError.visionModelUnavailable
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func createEarner(_ request: Components.Schemas.IncomeEarnerCreateRequest) async throws {
        switch try await client.createIncomeEarner(.init(body: .json(request))) {
        case .created:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw IncomeAPIError.forbidden
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func analysis() async throws -> Components.Schemas.IncomeAnalysisResponse {
        switch try await client.getIncomeAnalysis(.init()) {
        case .ok(let response):
            return try response.body.json
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func deleteEarner(id: String) async throws {
        switch try await client.deleteIncomeEarner(.init(path: .init(earnerId: id))) {
        case .noContent:
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw IncomeAPIError.forbidden
        case .notFound:
            throw APIError.server(404)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
