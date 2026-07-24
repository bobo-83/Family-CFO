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
    // ADR 0034: the assigned role's name and resolved rights; screens gate with
    // these. Optional so a credential stored before rights shipped still decodes
    // (RolePolicy then falls back to the legacy role's preset).
    var roleName: String?
    var rights: [String]?
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

    /// Shared bank-data freshness, shown identically on every synced screen (M103).
    let syncStatus = SyncStatusModel()

    /// Memoizes a month's transactions so category drill-downs don't re-fetch the
    /// whole month each time (M105).
    let monthTransactions = MonthTransactionsCache()

    var rolePolicy: RolePolicy {
        RolePolicy(role: credential?.role, rights: credential?.rights.map(Set.init))
    }

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

    /// The Bills tab — suggestions, current bills, add/delete, and bank sync
    /// (M90/M95).
    var bills: BillsAPI? {
        client.map { LiveBillsAPI(client: $0) }
    }

    /// Debts & loans — add/list installment loans (M96).
    var budgetsAPI: BudgetsAPI? {
        client.map { LiveBudgetsAPI(client: $0) }
    }

    var goalsAPI: GoalsAPI? {
        client.map { LiveGoalsAPI(client: $0) }
    }

    var debts: DebtsAPI? {
        client.map { LiveDebtsAPI(client: $0) }
    }

    /// Review queue — possible duplicate charges to keep/dispute/delete (M97).
    var review: ReviewAPI? {
        client.map { LiveReviewAPI(client: $0) }
    }

    /// Off-box backups — configure a Synology share, schedule, restore (M98).
    var backups: BackupAPI? {
        client.map { LiveBackupAPI(client: $0) }
    }

    /// Accounts — where the money is, and emergency-fund designation (M99).
    var accounts: AccountsAPI? {
        client.map { LiveAccountsAPI(client: $0) }
    }

    /// The shared transaction-detail surface — category, note, check photo (M100).
    var transactionDetail: TransactionDetailAPI? {
        client.map { LiveTransactionDetailAPI(client: $0) }
    }

    /// The Activity/History log with durable undo (M101).
    var activity: ActivityAPI? {
        client.map { LiveActivityAPI(client: $0) }
    }

    /// Advisor study coverage — what the AI has learned from the history (ADR 0040).
    var aiStudy: AiStudyAPI? {
        client.map { LiveAiStudyAPI(client: $0) }
    }

    var aiRuntime: AIRuntimeAPI? {
        client.map { LiveAIRuntimeAPI(client: $0) }
    }

    /// Box-level operator roster (ADR 0065) — parity with the dashboard's
    /// Users page section.
    var systemAdmins: SystemAdminsAPI? {
        client.map { LiveSystemAdminsAPI(client: $0) }
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
            await refreshSessionRights()
            // M-watch (ADR 0067): keep the watch's copy of the pairing fresh.
            PhoneWatchBridge.shared.activate()
            PhoneWatchBridge.shared.push(server: server, credential: credential)
        }
    }

    /// ADR 0065: rights change server-side (role edits, system-admin grants)
    /// while the stored credential keeps its pairing-time snapshot — without
    /// this, a freshly granted system admin would never see the new screens.
    /// Best-effort: offline keeps the cached rights, and every server check
    /// is per-request anyway.
    func refreshSessionRights() async {
        guard let client, var credential else { return }
        guard case .ok(let response) = try? await client.getSessionInfo(.init()),
            let info = try? response.body.json
        else { return }
        guard credential.rights != info.rights || credential.roleName != info.roleName else {
            return
        }
        credential.rights = info.rights
        credential.roleName = info.roleName
        if let data = try? JSONEncoder().encode(credential) {
            try? KeychainStore.save(data, account: Self.credentialAccount)
        }
        self.credential = credential
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
        PhoneWatchBridge.shared.activate()
        PhoneWatchBridge.shared.push(server: server, credential: credential)
    }

    /// Forgets the pairing locally. Revoking the credential server-side
    /// happens on the dashboard's Devices page (owner-only).
    func unpair() {
        PhoneWatchBridge.shared.push(server: nil, credential: nil)
        KeychainStore.delete(account: Self.credentialAccount)
        UserDefaults.standard.removeObject(forKey: Self.serverDefaultsKey)
        server = nil
        credential = nil
        phase = .unpaired
    }

    /// ADR 0056: sign out WITHOUT unpairing — drops the credential (revoking
    /// its session server-side, best-effort) but keeps the server address and
    /// pinned certificate, so signing back in is just email + password on the
    /// login screen (or scanning a fresh QR). Enables switching members on a
    /// shared device.
    func signOut() async {
        if let client {
            _ = try? await client.deleteAuthSession(.init())
        }
        PhoneWatchBridge.shared.push(server: server, credential: nil)
        KeychainStore.delete(account: Self.credentialAccount)
        KeychainStore.delete(account: "device-private-key")
        credential = nil
        phase = .unpaired
    }
}
