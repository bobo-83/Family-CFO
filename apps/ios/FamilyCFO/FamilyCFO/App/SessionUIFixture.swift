#if DEBUG && targetEnvironment(simulator)
import CryptoKit
import Foundation
import HTTPTypes
import OpenAPIRuntime

/// Exercises the production root and sign-in UI without network, real
/// credentials, Watch connectivity or persisted household data.
@MainActor
final class SessionUIFixture {
    let model: AppModel
    let login: LoginViewModel
    private let expires: Bool
    private let server = ServerConfig(
        apiBaseURL: URL(string: "https://fixture.invalid/api/v1")!,
        certificateSHA256: nil, householdID: "synthetic-household",
        householdName: "Synthetic household", deviceName: "Simulator")

    static func fromLaunchArguments() -> SessionUIFixture? {
        let arguments = ProcessInfo.processInfo.arguments
        if arguments.contains("--ui-expiring-session") { return SessionUIFixture(expires: true) }
        if arguments.contains("--ui-offline-session") { return SessionUIFixture(expires: false) }
        return nil
    }

    private init(expires: Bool) {
        self.expires = expires
        let key = P256.Signing.PrivateKey().rawRepresentation
        let transport = SessionFixtureTransport()
        let storage = AppSessionStorage(
            readServer: { nil }, writeServer: { _ in },
            readLanguage: { "en" }, writeLanguage: { _ in },
            readCredential: { $0 == "device-private-key" ? key : nil },
            writeCredential: { _, _ in }, deleteCredential: { _ in },
            makeSyncStatus: { _ in SyncStatusModel() })
        let effects = AppSessionEffects(
            registerShortcuts: {}, clearArtifacts: { _ in },
            activateWatch: {}, pushWatch: { _, _ in }, revokeSession: { _ in })
        model = AppModel(storage: storage, effects: effects, transport: transport)
        login = LoginViewModel(buildClient: { url, pin in
            APIClientFactory.makeClient(baseURL: url, pinnedCertificateSHA256: pin, transport: transport)
        })
    }

    func start() {
        guard model.phase == .loading else { return }
        model.completePairing(server: server, credential: StoredCredential(
            deviceID: "synthetic-device", accessToken: "synthetic-initial-bearer",
            expiresAt: expires ? Date().addingTimeInterval(9) : .distantFuture,
            role: .adult, roleName: nil, rights: ["finances.view"]))
    }
}

private struct SessionFixtureTransport: ClientTransport {
    func send(
        _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        let headers: HTTPFields = [.contentType: "application/json"]
        switch operationID {
        case Operations.CreateDeviceReauthenticationChallenge.id:
            return (HTTPResponse(status: .created, headerFields: headers),
                    HTTPBody(#"{"challenge":"AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"}"#))
        case Operations.ReauthenticateDevice.id:
            guard let body else { throw URLError(.badServerResponse) }
            let data = try await Data(collecting: body, upTo: 4096)
            let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            guard payload?["password"] as? String == "synthetic-password" else {
                return (HTTPResponse(status: .unauthorized, headerFields: headers),
                        HTTPBody(#"{"error":{"code":"http_error","message":"Synthetic rejection"}}"#))
            }
            return (HTTPResponse(status: .created, headerFields: headers), HTTPBody(
                #"{"device_id":"synthetic-device","access_token":"synthetic-renewed-bearer","expires_at":"2099-01-01T00:00:00Z","role":"adult","rights":["finances.view"],"household_id":"synthetic-household"}"#
            ))
        default:
            throw URLError(.notConnectedToInternet)
        }
    }
}
#endif
