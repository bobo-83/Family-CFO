import Foundation
import Observation
import OpenAPIRuntime

/// The paired server, persisted in UserDefaults (nothing secret lives here —
/// the access token is in the Keychain).
struct ServerConfig: Codable, Equatable {
    var apiBaseURL: URL
    var certificateSHA256: String?
    var householdID: String
    var householdName: String
    var deviceName: String
    var retainedDeviceID: String? = nil
}

/// The revocable device credential stored in the Keychain. Existing QR-issued
/// credentials decode unchanged; new sessions come from `POST /pairing/login`.
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
    @ObservationIgnored private let storage: AppSessionStorage
    @ObservationIgnored private let effects: AppSessionEffects

    @ObservationIgnored private let wallNow: () -> Date
    @ObservationIgnored private let monotonicNow: () -> TimeInterval
    @ObservationIgnored private let transport: (any ClientTransport)?
    @ObservationIgnored private let authenticate: () async -> Bool
    @ObservationIgnored private var deadline: SessionDeadline?
    @ObservationIgnored private var expiryTask: Task<Void, Never>?

    init(
        storage: AppSessionStorage = .live, effects: AppSessionEffects = .live,
        wallNow: @escaping () -> Date = Date.init,
        monotonicNow: @escaping () -> TimeInterval = SessionDeadline.monotonicTime,
        transport: (any ClientTransport)? = nil,
        authenticate: @escaping () async -> Bool = { await BiometricGate.authenticate() }
    ) {
        self.storage = storage
        self.effects = effects
        self.wallNow = wallNow
        self.monotonicNow = monotonicNow
        self.authenticate = authenticate
        self.transport = transport
    }

    enum Phase: Equatable {
        case loading
        case unpaired
        case reauthenticating
        case locked
        case ready
    }

    enum SignInNotice: Equatable { case expired, rejected }

    private(set) var phase: Phase = .loading
    private(set) var signInNotice: SignInNotice?
    private(set) var sessionOwner = UUID()
    private(set) var startsAtOverview = false
    private(set) var needsHouseholdKeyRecovery = false
    private(set) var server: ServerConfig?
    /// A non-secret namespace for session-owned local reminders. Rotating it
    /// prevents an outgoing load from replacing or cancelling the new session's
    /// notification for a coincidentally identical bill id.
    private(set) var billNotificationScope = UUID().uuidString
    /// Set only during a cold, valid stored-session restoration. A fresh
    /// sign-in/account replacement must never inherit this language cache.
    @ObservationIgnored private var restoringStoredLanguage: String?
    private(set) var credential: StoredCredential?

    private func replaceCredential(_ next: StoredCredential?) {
        sessionOwner = UUID()
        credential = next
        needsHouseholdKeyRecovery = false
        householdCurrency.invalidate()
        monthTransactions.invalidate()
        advisorAsk = nil
        householdLanguage = restoringStoredLanguage ?? "en"
        syncStatus = storage.makeSyncStatus(next.map { "\(server?.householdID ?? ""):\($0.deviceID)" })
        billNotificationScope = UUID().uuidString
        effects.clearArtifacts(billNotificationScope)
        deadline = next.map {
            SessionDeadline(expiresAt: $0.expiresAt, wallNow: wallNow(), monotonicNow: monotonicNow())
        }
        checkSessionExpiry()
    }

    func checkSessionExpiry() {
        expiryTask?.cancel()
        expiryTask = nil
        guard let deadline else { return }
        let remaining = deadline.remaining(wallNow: wallNow(), monotonicNow: monotonicNow())
        guard remaining > 0 else {
            requireSignIn(.expired)
            return
        }
        expiryTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(min(remaining, 30))) }
            catch { return }
            self?.checkSessionExpiry()
        }
    }

    func ownsSession(_ owner: UUID) -> Bool {
        if let deadline,
            deadline.remaining(wallNow: wallNow(), monotonicNow: monotonicNow()) <= 0
        {
            requireSignIn(.expired)
        }
        return credential != nil && sessionOwner == owner
    }

    private func requireSignIn(_ reason: SignInNotice) {
        if let deviceID = credential?.deviceID { retainDevice(deviceID) }
        storage.deleteCredential(Self.credentialAccount)
        signInNotice = reason
        phase = server?.retainedDeviceID == nil ? .unpaired : .reauthenticating
        replaceCredential(nil)
        effects.activateWatch()
        effects.pushWatch(server, nil)
    }

    private func retainDevice(_ deviceID: String?) {
        guard var current = server, current.retainedDeviceID != deviceID else { return }
        current.retainedDeviceID = deviceID
        storage.writeServer(try? JSONEncoder().encode(current))
        server = current
    }

    func chooseDeviceSetup() {
        guard phase == .reauthenticating else { return }
        storage.deleteCredential("device-private-key")
        retainDevice(nil)
        signInNotice = nil
        sessionOwner = UUID()
        phase = .unpaired
    }

    func beginSignIn() -> UUID {
        sessionOwner = UUID()
        return sessionOwner
    }

    func reauthenticationKey() -> Data? { storage.readCredential("device-private-key") }

    /// #156 (ADR 0075): the household's base currency for the screens that must
    /// not guess it (Accounts and Goals forms). Seeded by every live context
    /// fetch through `LiveHouseholdAPI.onContext`, fetched once otherwise, and
    /// keyed to the paired household + session so it can never outlive them.
    @ObservationIgnored
    lazy var householdCurrency = HouseholdCurrencyProvider(
        sessionKey: { [weak self] in self?.currencySessionKey },
        fetch: { [weak self] in
            guard let household = self?.household else { throw APIError.unauthorized }
            return try await household.context(month: nil).currency
        }
    )

    private var currencySessionKey: String? {
        guard let server, credential != nil else { return nil }
        return "\(server.householdID):\(sessionOwner.uuidString)"
    }

    /// Request-start provenance for Apple aggregate loads. It intentionally
    /// combines household and credential/session identity; a response created
    /// before sign-out or re-pairing must never seed the new shell.
    var householdSessionIdentity: String? { currencySessionKey }

    /// Shared bank-data freshness, shown identically on every synced screen (M103).
    private(set) var syncStatus = SyncStatusModel()

    /// A grounded question another screen wants the advisor to answer — e.g. the
    /// Year chart's "explain this month's income" (ADR 0068). MainTabView jumps
    /// to the Advisor tab; ConversationListView consumes it into a new chat.
    /// UUID-stamped so asking the same question twice still fires onChange.
    struct AdvisorAsk: Equatable {
        let id: UUID
        let question: String
    }

    var advisorAsk: AdvisorAsk?

    func askAdvisor(_ question: String) {
        advisorAsk = AdvisorAsk(id: UUID(), question: question)
    }

    /// Memoizes a month's transactions so category drill-downs don't re-fetch the
    /// whole month each time (M105).
    let monthTransactions = MonthTransactionsCache()

    /// The household's advisor language ("en" | "vi" | "lt"), noted by the
    /// screens that already fetch `HouseholdContext` — Overview's load and the
    /// Settings language picker (#10 phase 1). The speech paths read it per
    /// utterance to pick a matching voice; nothing fetches it on the speak
    /// path. Server default is "en".
    /// #10: the household's language. Mirrored into UserDefaults on every set
    /// so the API client's Accept-Language middleware — which runs off the main
    /// actor and cannot touch this actor-isolated property — has a thread-safe
    /// source. The mirror also means the FIRST request after launch already
    /// carries the right language, before any context fetch returns.
    var householdLanguage = "en" {
        didSet { storage.writeLanguage(householdLanguage) }
    }

    nonisolated static let languageKey = "family-cfo.householdLanguage"

    var rolePolicy: RolePolicy {
        RolePolicy(role: credential?.role, rights: credential?.rights.map(Set.init))
    }

    // Internal (not private): PhoneWatchBridge reads persisted state directly
    // when the watch asks for the pairing from a background launch (ADR 0067 v6).
    nonisolated static let serverDefaultsKey = "family-cfo.server"
    nonisolated static let credentialAccount = "device-credential"

    /// M95: one per app shell — the streamed chat response's advertised
    /// recovery horizon, captured from raw headers by the middleware in every
    /// client this shell builds and read back by the advisor API when a
    /// stream fails mid-turn.
    private let advisorRecoveryHorizon = AdvisorRecoveryHorizon()

    /// The generated client for the paired server, or nil before pairing.
    /// The token is captured by value: the credential only changes at
    /// pairing/unpairing, which tears down and rebuilds the whole shell.
    private var client: Client? {
        guard let server, let credential else { return nil }
        let token = credential.accessToken
        let owner = sessionOwner
        let storage = storage
        return APIClientFactory.makeClient(
            baseURL: server.apiBaseURL,
            pinnedCertificateSHA256: server.certificateSHA256,
            token: { token },
            // ADR 0072 Phase 3: lets a sealed household's 423 answer trigger
            // one device unwrap-and-unlock before the request is replayed.
            devicePrivateKey: { storage.readCredential("device-private-key") },
            // #10 phase 4: the API writes its own prose (error details) in this
            // language. Read live from the cached household setting, so a
            // mid-session change applies to the next request.
            language: { storage.readLanguage() },
            advisorRecoveryHorizon: advisorRecoveryHorizon,
            ownsSession: { [weak self] in await self?.ownsSession(owner) ?? false },
            onUnauthorized: { [weak self] in await self?.rejectSession(owner) },
            onHouseholdLocked: { [weak self] in await self?.reportHouseholdLocked(owner) },
            transport: transport
        )
    }

    func rejectSession(_ owner: UUID) {
        guard ownsSession(owner) else { return }
        requireSignIn(.rejected)
    }

    private func reportHouseholdLocked(_ owner: UUID) {
        if ownsSession(owner) { needsHouseholdKeyRecovery = true }
    }

    var api: AdvisorAPI? {
        client.map { LiveAdvisorAPI(client: $0, recoveryHorizon: advisorRecoveryHorizon) }
    }

    /// The on-box natural voice (M87); nil before pairing, and 503-degrading to
    /// the system voice whenever the optional `tts` service isn't there.
    var speechAudio: SpeechAudioAPI? {
        client.map { LiveSpeechAudioAPI(client: $0) }
    }

    /// The daily-glance context behind the Overview tab (M88). Every live
    /// context fetch — Overview, Settings, whichever runs first — seeds the
    /// household-language cache, because the app launches into the Advisor
    /// tab where nothing else would have loaded it yet (#10).
    var household: HouseholdAPI? {
        guard let client, let server else { return nil }
        // #156 (review of #158): a live API built for THIS session may answer
        // after a sign-out and a pairing as another household — the untracked
        // language-seeding fetch on unlock makes that reachable with no view
        // alive. The callback therefore carries the session and household it
        // was built for and is dropped for any other.
        let requestedIn = currencySessionKey
        let owner = sessionOwner
        let householdID = server.householdID
        return LiveHouseholdAPI(client: client) { [weak self] context in
            guard let self, self.ownsSession(owner), self.currencySessionKey == requestedIn,
                context.householdId == householdID
            else { return }
            self.needsHouseholdKeyRecovery = false
            self.householdLanguage = context.language ?? "en"
            // The same fetch carries the base currency.
            if let requestedIn {
                self.householdCurrency.seed(context.currency, requestedIn: requestedIn)
            }
        }
    }

    /// W-2 scan and earner creation behind the camera flows (M89).
    var income: IncomeAPI? {
        client.map { LiveIncomeAPI(client: $0) }
    }

    /// Quick transaction categorization (M91).
    var categorize: CategorizeAPI? {
        client.map { LiveCategorizeAPI(client: $0) }
    }

    /// #29: record a transaction by hand — cash, or a statement charge the bank
    /// feed never delivered (#25).
    var addTransaction: AddTransactionAPI? {
        client.map { LiveAddTransactionAPI(client: $0) }
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

    /// Paired-device roster (ADR 0025 parity with the dashboard's Devices page).
    var devices: DevicesAPI? {
        client.map { LiveDevicesAPI(client: $0) }
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

    /// #97: your own password. No right gates it — everybody has one. The
    /// server keeps the calling session alive, so the stored credential stays
    /// valid and nothing here needs rewriting.
    var changePassword: ChangePasswordAPI? {
        client.map { LiveChangePasswordAPI(client: $0) }
    }

    /// Hosted-household roster (#180) — parity with the dashboard's
    /// Households page. System admins only.
    var hostedHouseholds: HouseholdsAPI? {
        client.map { LiveHouseholdsAPI(client: $0) }
    }

    func bootstrap() {
        guard phase == .loading else { return }
        // Re-register the Siri App Shortcuts on every launch: rapid TestFlight
        // updates can silently drop the registration, breaking "Ask my CFO"
        // until a refresh (user report 2026-07-26). Cheap and idempotent.
        effects.registerShortcuts()
        restoreSession(
            serverData: storage.readServer(),
            credentialData: storage.readCredential(Self.credentialAccount)
        )
        if phase == .locked {
            // ADR 0067 v6: listen for the watch's credential requests from the
            // very first launch moment, not only after an unlock.
            effects.activateWatch()
        }
    }

    /// Server settings survive a signed-out relaunch, independently of a token.
    /// This seam also tests old credential decoding without touching Keychain.
    func restoreSession(serverData: Data?, credentialData: Data?) {
        let restoredServer = serverData.flatMap {
            try? JSONDecoder().decode(ServerConfig.self, from: $0)
        }
        struct DeviceIdentity: Decodable { let deviceID: String }
        let restoredCredential = credentialData.flatMap {
            try? JSONDecoder().decode(StoredCredential.self, from: $0)
        }
        server = restoredServer
        if let restoredCredential, restoredServer != nil,
            restoredCredential.expiresAt.timeIntervalSinceReferenceDate.isFinite,
            restoredCredential.expiresAt > wallNow()
        {
            if phase == .loading { restoringStoredLanguage = storage.readLanguage() ?? "en" }
            defer { restoringStoredLanguage = nil }
            phase = .locked
            if credential?.accessToken == restoredCredential.accessToken {
                // Same token, possibly a different expiry. The deadline must
                // track the supplied credential, not the one first installed —
                // an earlier expiry must not be lived past (PR #169 review).
                // replaceCredential would be wrong here: it drops the caches
                // that same-token restoration intentionally preserves.
                credential = restoredCredential
                deadline = SessionDeadline(
                    expiresAt: restoredCredential.expiresAt,
                    wallNow: wallNow(), monotonicNow: monotonicNow()
                )
                checkSessionExpiry()
            } else {
                replaceCredential(restoredCredential)
            }
            return
        }
        if restoredServer != nil, let credentialData {
            if let identity = try? JSONDecoder().decode(DeviceIdentity.self, from: credentialData) {
                retainDevice(identity.deviceID)
            }
            requireSignIn(restoredCredential == nil ? .rejected : .expired)
            return
        }
        if credential != nil { replaceCredential(nil) }
        householdLanguage = "en"
        phase = server?.retainedDeviceID == nil ? .unpaired : .reauthenticating
    }

    /// Latest-wins re-push whenever the app comes forward (ADR 0067 v6).
    func pushWatchPairing() {
        guard phase == .ready, ownsSession(sessionOwner) else { return }
        effects.pushWatch(server, credential)
    }

    func unlock() async {
        let owner = sessionOwner
        guard phase == .locked, ownsSession(owner), await authenticate(),
            phase == .locked, ownsSession(owner) else { return }
        phase = .ready
        await refreshSessionRights()
        guard ownsSession(owner) else { return }
        seedHouseholdLanguage()
        effects.activateWatch()
        effects.pushWatch(server, credential)
    }

    /// ADR 0065: rights change server-side (role edits, system-admin grants)
    /// while the stored credential keeps its pairing-time snapshot — without
    /// this, a freshly granted system admin would never see the new screens.
    /// Best-effort: offline keeps the cached rights, and every server check
    /// is per-request anyway.
    func refreshSessionRights() async {
        let owner = sessionOwner
        guard let client, var credential else { return }
        guard case .ok(let response) = try? await client.getSessionInfo(.init()),
            let info = try? response.body.json
        else { return }
        // A response to the old device must never resurrect it after sign-out.
        guard ownsSession(owner) else { return }
        guard credential.rights != info.rights || credential.roleName != info.roleName else {
            return
        }
        credential.rights = info.rights
        credential.roleName = info.roleName
        if let data = try? JSONEncoder().encode(credential) {
            try? storage.writeCredential(data, Self.credentialAccount)
        }
        self.credential = credential
    }

    @discardableResult
    func completePairing(server: ServerConfig, credential: StoredCredential) -> Bool {
        var server = server
        server.retainedDeviceID = credential.deviceID
        guard credential.expiresAt > wallNow(), let data = try? JSONEncoder().encode(server),
            let credentialData = try? JSONEncoder().encode(credential),
            (try? storage.writeCredential(credentialData, Self.credentialAccount)) != nil
        else {
            return false
        }
        storage.writeServer(data)
        self.server = server
        startsAtOverview = phase == .reauthenticating || signInNotice != nil
        signInNotice = nil
        phase = .ready
        replaceCredential(credential)
        effects.activateWatch()
        effects.pushWatch(server, credential)
        seedHouseholdLanguage()
        return true
    }

    /// One best-effort context fetch per session so the language cache is
    /// seeded even when no context screen ever loads — the launch tab is the
    /// Advisor, where a read-aloud tap can come first (#10, user report
    /// 2026-08-07). The fetch itself lands in `householdLanguage` through
    /// LiveHouseholdAPI's onContext; failure keeps the "en" default. Never
    /// called on the speak path.
    private func seedHouseholdLanguage() {
        // Piggyback: warm the NL model here, off the speak path, so the first
        // read-aloud never pays the recognizer's model load.
        Task.detached(priority: .utility) { UtteranceLanguage.warmUp() }
        Task { _ = try? await household?.context(month: nil) }
    }

    /// Forgets the pairing locally. Revoking the credential server-side
    /// happens on the Devices screen (in-app or dashboard, devices.manage).
    func unpair() {
        effects.pushWatch(nil, nil)
        storage.deleteCredential(Self.credentialAccount)
        storage.deleteCredential("device-private-key")
        storage.writeServer(nil)
        server = nil
        signInNotice = nil
        replaceCredential(nil)
        phase = .unpaired
    }

    /// ADR 0079: sign out but preserve server address/trust across relaunch.
    /// Clear local access before awaiting best-effort server revocation.
    func signOut() async {
        let outgoingClient: Client? = if let server, let token = credential?.accessToken {
            APIClientFactory.makeClient(
                baseURL: server.apiBaseURL, pinnedCertificateSHA256: server.certificateSHA256,
                token: { token }, transport: transport)
        } else { nil }
        retainDevice(nil)
        effects.pushWatch(server, nil)
        storage.deleteCredential(Self.credentialAccount)
        storage.deleteCredential("device-private-key")
        signInNotice = nil
        replaceCredential(nil)
        phase = .unpaired
        if let outgoingClient {
            await effects.revokeSession(outgoingClient)
        }
    }
}
