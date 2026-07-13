import Foundation
import Observation

/// The paired server, persisted in UserDefaults (nothing secret lives here —
/// the access token is in the Keychain).
struct ServerConfig: Codable, Equatable {
    var apiBaseURL: URL
    var certificateSHA256: String?
    var householdID: String
    var householdName: String
    var deviceName: String
}

/// The revocable device credential from `POST /pairing/confirm`, stored in
/// the Keychain (M83). `role` is who the device acts as — the pairing
/// session's creator.
struct StoredCredential: Codable, Equatable {
    var deviceID: String
    var accessToken: String
    var expiresAt: Date
    var role: HouseholdRole?
}

@MainActor
@Observable
final class AppModel {
    enum Phase: Equatable {
        case loading
        case unpaired
        case locked
        case ready
    }

    private(set) var phase: Phase = .loading
    private(set) var server: ServerConfig?
    private(set) var credential: StoredCredential?

    var rolePolicy: RolePolicy { RolePolicy(role: credential?.role) }

    private static let serverDefaultsKey = "family-cfo.server"
    private static let credentialAccount = "device-credential"

    /// The generated client for the paired server, or nil before pairing.
    /// The token is captured by value: the credential only changes at
    /// pairing/unpairing, which tears down and rebuilds the whole shell.
    private var client: Client? {
        guard let server, let credential else { return nil }
        let token = credential.accessToken
        return APIClientFactory.makeClient(
            baseURL: server.apiBaseURL,
            pinnedCertificateSHA256: server.certificateSHA256,
            token: { token }
        )
    }

    var api: AdvisorAPI? {
        client.map { LiveAdvisorAPI(client: $0) }
    }

    /// The on-box natural voice (M87); nil before pairing, and 503-degrading to
    /// the system voice whenever the optional `tts` service isn't there.
    var speechAudio: SpeechAudioAPI? {
        client.map { LiveSpeechAudioAPI(client: $0) }
    }

    /// The daily-glance context behind the Overview tab (M88).
    var household: HouseholdAPI? {
        client.map { LiveHouseholdAPI(client: $0) }
    }

    /// W-2 scan and earner creation behind the camera flows (M89).
    var income: IncomeAPI? {
        client.map { LiveIncomeAPI(client: $0) }
    }

    /// Quick transaction categorization (M91).
    var categorize: CategorizeAPI? {
        client.map { LiveCategorizeAPI(client: $0) }
    }

    func bootstrap() {
        if let data = UserDefaults.standard.data(forKey: Self.serverDefaultsKey),
            let server = try? JSONDecoder().decode(ServerConfig.self, from: data),
            let credentialData = KeychainStore.load(account: Self.credentialAccount),
            let credential = try? JSONDecoder().decode(StoredCredential.self, from: credentialData)
        {
            self.server = server
            self.credential = credential
            phase = .locked
        } else {
            phase = .unpaired
        }
    }

    func unlock() async {
        guard phase == .locked else { return }
        if await BiometricGate.authenticate() {
            phase = .ready
        }
    }

    func completePairing(server: ServerConfig, credential: StoredCredential) {
        guard let data = try? JSONEncoder().encode(server),
            let credentialData = try? JSONEncoder().encode(credential),
            (try? KeychainStore.save(credentialData, account: Self.credentialAccount)) != nil
        else {
            return
        }
        UserDefaults.standard.set(data, forKey: Self.serverDefaultsKey)
        self.server = server
        self.credential = credential
        phase = .ready
    }

    /// Forgets the pairing locally. Revoking the credential server-side
    /// happens on the dashboard's Devices page (owner-only).
    func unpair() {
        KeychainStore.delete(account: Self.credentialAccount)
        UserDefaults.standard.removeObject(forKey: Self.serverDefaultsKey)
        server = nil
        credential = nil
        phase = .unpaired
    }
}
