import Foundation

/// The household's paired-device roster (ADR 0025 parity with the dashboard's
/// Devices page). Listing takes only membership; revoking is gated by
/// `devices.manage` and, beyond ending the device's sessions, destroys its
/// wrap of the household data key (ADR 0072).
protocol DevicesAPI: Sendable {
    func list() async throws -> [Components.Schemas.PairedDevice]
    func revoke(deviceID: String) async throws
}

struct LiveDevicesAPI: DevicesAPI {
    let client: Client

    func list() async throws -> [Components.Schemas.PairedDevice] {
        switch try await client.listPairedDevices(.init()) {
        case .ok(let response):
            return try response.body.json.devices
        case .unauthorized:
            throw APIError.unauthorized
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }

    func revoke(deviceID: String) async throws {
        switch try await client.revokePairedDevice(.init(path: .init(deviceId: deviceID))) {
        case .noContent:
            return
        case .notFound:
            // Already gone — the caller wanted it revoked.
            return
        case .unauthorized:
            throw APIError.unauthorized
        case .forbidden:
            throw APIError.server(403)
        case .conflict(let error):
            // The server refused — revoking the device this session runs on.
            // Its message says what to do; surface it verbatim.
            if let message = try? error.body.json.error.message {
                throw APIError.conflict(message)
            }
            throw APIError.server(409)
        case .undocumented(let status, _):
            throw APIError.server(status)
        }
    }
}
