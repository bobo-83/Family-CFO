import CryptoKit
import Foundation
import HTTPTypes
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

@MainActor
struct SessionExpiryTests {
    private let endpoint = URL(string: "https://box.invalid:8443/api/v1")!
    private let deviceID = "synthetic-device"
    private let now = Date(timeIntervalSince1970: 2_000_000_000)

    private func makeModel(_ harness: SessionTestHarness) -> AppModel {
        // Freeze both clocks so a slow simulator cannot expire a session while
        // a test is exercising rejection, relogin, or a transport failure.
        AppModel(storage: harness.storage, effects: harness.effects,
            wallNow: { now }, monotonicNow: { 0 }, transport: OfflineSessionTransport())
    }

    private func server() -> ServerConfig {
        ServerConfig(apiBaseURL: endpoint, certificateSHA256: "saved-pin",
            householdID: "household", householdName: "Synthetic household", deviceName: "Phone")
    }

    private func credential(until: Date, token: String = "synthetic-bearer") -> StoredCredential {
        StoredCredential(deviceID: deviceID, accessToken: token, expiresAt: until,
            role: .adult, roleName: nil, rights: nil)
    }

    private func restore(_ model: AppModel, expiry: Date) throws {
        let encoder = JSONEncoder()
        model.restoreSession(serverData: try encoder.encode(server()),
            credentialData: try encoder.encode(credential(until: expiry)))
    }

    @Test func startupExpiryClearsBearerAndPreservesDeviceTrustAndKey() throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let key = P256.Signing.PrivateKey().rawRepresentation
        try harness.credentials.write(key, account: "device-private-key")
        let model = makeModel(harness)
        try restore(model, expiry: now)

        #expect(model.phase == .reauthenticating)
        #expect(model.signInNotice == .expired)
        #expect(model.credential == nil)
        #expect(model.server?.retainedDeviceID == deviceID)
        #expect(model.server?.apiBaseURL == endpoint)
        #expect(model.server?.certificateSHA256 == "saved-pin")
        #expect(model.reauthenticationKey() == key)
        #expect(harness.watchDeviceIDs == [nil])

        let reopened = makeModel(harness)
        reopened.bootstrap()
        #expect(reopened.phase == .reauthenticating)
        #expect(reopened.signInNotice == nil)
        #expect(reopened.server?.retainedDeviceID == deviceID)
    }

    @Test func expiryDuringBiometricsNeverEntersProtectedRoot() async throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let clock = TestClock(now: now)
        let model = AppModel(storage: harness.storage, effects: harness.effects,
            wallNow: { clock.now }, monotonicNow: { clock.elapsed }, authenticate: {
                clock.elapsed += 11
                return true
            })
        try restore(model, expiry: now.addingTimeInterval(10))

        await model.unlock()

        #expect(model.phase == .reauthenticating)
        #expect(model.signInNotice == .expired)
        #expect(model.credential == nil)
    }

    @Test func staleUnauthorizedCannotRejectAReinstalledSameToken() throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = makeModel(harness)
        try restore(model, expiry: now.addingTimeInterval(60))
        let originalOwner = model.sessionOwner
        model.rejectSession(originalOwner)
        #expect(model.signInNotice == .rejected)
        #expect(model.completePairing(server: server(), credential: credential(until: now.addingTimeInterval(120))))
        model.rejectSession(originalOwner)

        #expect(model.phase == .ready)
        #expect(model.startsAtOverview)
        #expect(model.signInNotice == nil)
        #expect(model.credential?.accessToken == "synthetic-bearer")
    }

    @Test func nestedUnlockUnauthorizedEndsTheSessionWithoutReplayingTheWrite() async throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = makeModel(harness)
        try restore(model, expiry: now.addingTimeInterval(60))
        let owner = model.sessionOwner
        let bearer = BearerAuthMiddleware(token: { "synthetic-bearer" },
            ownsSession: { await model.ownsSession(owner) },
            onUnauthorized: { await model.rejectSession(owner) })
        let unlock = HouseholdUnlockMiddleware(devicePrivateKey: { Data([1]) })
        let transport = LockedThenRejectedTransport()

        let (response, _) = try await unlock.intercept(
            HTTPRequest(method: .post, scheme: nil, authority: nil, path: "/transactions"),
            body: HTTPBody("synthetic-write"), baseURL: endpoint, operationID: "testWrite"
        ) { request, body, url in
            try await bearer.intercept(request, body: body, baseURL: url, operationID: "testWrite") {
                request, _, _ in await transport.respond(to: request)
            }
        }

        #expect(response.status.code == 423)
        #expect(await transport.paths == ["/transactions", "/household/device-wrap"])
        #expect(model.phase == .reauthenticating)
        #expect(model.signInNotice == .rejected)
    }

    @Test func sameTokenRestorationAdoptsTheNewlySuppliedExpiry() throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let clock = TestClock(now: now)
        let model = AppModel(storage: harness.storage, effects: harness.effects,
            wallNow: { clock.now }, monotonicNow: { clock.elapsed },
            transport: OfflineSessionTransport())
        try restore(model, expiry: now.addingTimeInterval(60))
        // Keychain restore of the SAME bearer with an earlier expiry (the
        // server clamped it): the deadline must track the new credential.
        try restore(model, expiry: now.addingTimeInterval(10))
        #expect(model.phase == .locked)
        #expect(model.credential?.expiresAt == now.addingTimeInterval(10))

        clock.elapsed += 11
        model.checkSessionExpiry()

        #expect(model.phase == .reauthenticating)
        #expect(model.signInNotice == .expired)
        #expect(model.credential == nil)
    }

    @Test func verifiesServerWireVectorAndSignsTheSameTranscript() throws {
        let device = "00000000-0000-4000-8000-000000000164"
        let challenge = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
        let rawKey = Data(repeating: 0, count: 31) + Data([1])
        let key = try P256.Signing.PrivateKey(rawRepresentation: rawKey)
        let message = Data("family-cfo/device-reauth/v1\n\(device)\n\(challenge)".utf8)
        let fixed = "k0W1YxjHtcknC23YxrA1h7_Vz6sSSud8QYL2O7M87g1XZERRTUVkwsVPezlVnR5QNFIFF_11z6LaW7F_8ws3mw"
        func decode(_ value: String) throws -> P256.Signing.ECDSASignature {
            let raw = try #require(Data(base64Encoded: value
                .replacingOccurrences(of: "-", with: "+")
                .replacingOccurrences(of: "_", with: "/") + "=="))
            return try P256.Signing.ECDSASignature(rawRepresentation: raw)
        }
        #expect(key.publicKey.isValidSignature(try decode(fixed), for: message))
        let signed = try LoginViewModel.signReauthenticationChallenge(challenge, deviceID: device, signer: key)
        #expect(key.publicKey.isValidSignature(try decode(signed), for: message))
    }

    @Test func transportFailureDoesNotEndTheSession() async throws {
        let harness = SessionTestHarness()
        defer { harness.cleanup() }
        let model = makeModel(harness)
        try restore(model, expiry: now.addingTimeInterval(60))
        let owner = model.sessionOwner
        let bearer = BearerAuthMiddleware(token: { "synthetic-bearer" },
            ownsSession: { await model.ownsSession(owner) },
            onUnauthorized: { await model.rejectSession(owner) })
        do {
            _ = try await bearer.intercept(
                HTTPRequest(method: .get, scheme: nil, authority: nil, path: "/household"),
                body: nil, baseURL: endpoint, operationID: "getHousehold"
            ) { _, _, _ in throw URLError(.notConnectedToInternet) }
            Issue.record("Expected a transport failure")
        } catch let error as URLError {
            #expect(error.code == .notConnectedToInternet)
        }
        #expect(model.phase == .locked)
        #expect(model.signInNotice == nil)
        #expect(model.ownsSession(owner))
    }
}

@MainActor
private final class TestClock {
    let now: Date
    var elapsed: TimeInterval = 0
    init(now: Date) { self.now = now }
}

private actor LockedThenRejectedTransport {
    private(set) var paths: [String] = []

    func respond(to request: HTTPRequest) -> (HTTPResponse, HTTPBody?) {
        paths.append(request.path ?? "")
        return (HTTPResponse(status: .init(code: paths.count == 1 ? 423 : 401)), nil)
    }
}

private struct OfflineSessionTransport: ClientTransport {
    func send(
        _ request: HTTPRequest, body: HTTPBody?, baseURL: URL, operationID: String
    ) async throws -> (HTTPResponse, HTTPBody?) {
        throw URLError(.notConnectedToInternet)
    }
}
