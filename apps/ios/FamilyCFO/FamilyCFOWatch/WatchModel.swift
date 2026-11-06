import Foundation
import Observation
import WatchConnectivity
import WidgetKit

/// The watch's little world: the credential relayed from the phone
/// (WatchConnectivity application context), persisted so the app works
/// standalone once paired. Revoking the device on the dashboard kills the
/// token server-side; signing out on the phone pushes an empty context here.
@MainActor
@Observable
final class WatchModel {
    private(set) var apiBaseURL: URL?
    private(set) var certificateSHA256: String?
    private(set) var token: String?
    private(set) var householdName: String?
    /// Atomic owner key for every Watch load. It advances once after all pairing
    /// fields have been replaced and the resulting session identity changed.
    private(set) var sessionRevision: UInt64 = 0

    var isPaired: Bool { apiBaseURL != nil && token != nil }

    private(set) var sessionIdentity: String?
    private(set) var expiresAt: Date?
    @ObservationIgnored private var order: WatchContextOrder?
    @ObservationIgnored private var deadline: SessionDeadline?
    @ObservationIgnored private var expiryTask: Task<Void, Never>?

    private let advisorRecoveryHorizon = AdvisorRecoveryHorizon()

    var client: Client? {
        guard let apiBaseURL, let token, let identity = sessionIdentity else { return nil }
        return APIClientFactory.makeClient(
            baseURL: apiBaseURL,
            pinnedCertificateSHA256: certificateSHA256,
            token: { token },
            advisorRecoveryHorizon: advisorRecoveryHorizon,
            ownsSession: { [weak self] in await self?.owns(identity) ?? false },
            onUnauthorized: { [weak self] in await self?.reject(identity) }
        )
    }

    var advisor: AdvisorAPI? {
        client.map { LiveAdvisorAPI(client: $0, recoveryHorizon: advisorRecoveryHorizon) }
    }
    var speech: SpeechAudioAPI? { client.map { LiveSpeechAudioAPI(client: $0) } }

    private let connectivity = WatchConnectivityReceiver()

    init() {
        load()
        connectivity.onContext = { [weak self] context in
            Task { @MainActor in self?.apply(context) }
        }
        connectivity.activate()
    }

    private static let defaultsKey = "family-cfo.watch.pairing"

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: Self.defaultsKey),
            let stored = try? JSONDecoder().decode([String: String].self, from: data),
            WatchContextOrder(stored) != nil
        else {
            UserDefaults.standard.removeObject(forKey: Self.defaultsKey)
            updateSnapshotAccess(clear: true)
            return
        }
        apply(stored, restoring: true)
    }

    func apply(_ context: [String: String], restoring: Bool = false, allowNewScope: Bool = false) {
        guard let incoming = WatchContextOrder(context),
            order?.accepts(incoming, allowNewScope: allowNewScope) ?? true
        else { return }
        switch context["state"] {
        case "active":
            guard let seconds = context["expiresAt"].flatMap(Double.init), seconds.isFinite,
                let url = context["apiBaseURL"].flatMap(URL.init(string:)),
                url.scheme?.lowercased() == "https",
                let token = context["token"], !token.isEmpty else { return }
            order = incoming
            let expiresAt = Date(timeIntervalSince1970: seconds)
            if expiresAt <= Date() {
                invalidate()
                return
            }
            apiBaseURL = url
            certificateSHA256 = CertificatePin.normalizedPin(context["certificateSHA256"])
            self.token = token
            householdName = context["householdName"]
            self.expiresAt = expiresAt
            deadline = SessionDeadline(
                expiresAt: expiresAt, wallNow: Date(), monotonicNow: SessionDeadline.monotonicTime())
            sessionIdentity = "\(incoming.scope.uuidString):\(incoming.sequence)"
            sessionRevision &+= 1
            persist(context)
            updateSnapshotAccess(clear: !restoring)
            checkExpiry()
        case "invalidated":
            order = incoming
            invalidate()
        default:
            return
        }
    }

    private func persist(_ context: [String: String]) {
        if let data = try? JSONEncoder().encode(context) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
    }

    private func updateSnapshotAccess(clear: Bool) {
        let store = WatchFaceSnapshotStore()
        if clear { store.clear() }
        store.sessionExpiry = expiresAt
        WidgetCenter.shared.reloadAllTimelines()
    }

    private func invalidate() {
        expiryTask?.cancel()
        expiryTask = nil
        token = nil
        apiBaseURL = nil
        certificateSHA256 = nil
        householdName = nil
        expiresAt = nil
        deadline = nil
        sessionIdentity = nil
        sessionRevision &+= 1
        if let order {
            persist(["version": "1", "state": "invalidated", "scope": order.scope.uuidString,
                "sequence": String(order.sequence)])
        }
        updateSnapshotAccess(clear: true)
    }

    func checkExpiry() {
        expiryTask?.cancel()
        guard let deadline else { return }
        let remaining = deadline.remaining(wallNow: Date(), monotonicNow: SessionDeadline.monotonicTime())
        guard remaining > 0 else { invalidate(); return }
        expiryTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(min(remaining, 30))) }
            catch { return }
            self?.checkExpiry()
        }
    }

    private func owns(_ identity: String) -> Bool {
        if let deadline,
            deadline.remaining(wallNow: Date(), monotonicNow: SessionDeadline.monotonicTime()) <= 0
        {
            invalidate()
        }
        return sessionIdentity == identity
    }

    private func reject(_ identity: String) {
        guard owns(identity) else { return }
        invalidate()
        let revision = sessionRevision
        Task {
            guard let context = await connectivity.requestContext(), revision == sessionRevision else { return }
            apply(context)
        }
    }

    func reconnectToPhone() async {
        let revision = sessionRevision
        guard !isPaired, let context = await connectivity.requestContext(),
            revision == sessionRevision else { return }
        // Only a deliberate live reconnect can establish a new phone scope.
        // Delayed queued contexts from the previous phone are then ignored.
        apply(context, allowNewScope: true)
    }

}

/// WCSession plumbing kept out of the observable model: the delegate fires on
/// arbitrary queues and only forwards the typed context dictionary.
final class WatchConnectivityReceiver: NSObject, WCSessionDelegate, @unchecked Sendable {
    var onContext: (([String: String]) -> Void)?

    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
    }

    private func forward(_ context: [String: Any]) {
        let typed = context.compactMapValues { $0 as? String }
        guard !typed.isEmpty else { return }
        onContext?(typed)
    }

    func session(
        _ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        // The context the phone last pushed is available immediately.
        forward(session.receivedApplicationContext)
    }

    func session(_ session: WCSession, didReceiveApplicationContext context: [String: Any]) {
        forward(context)
    }

    /// Live round-trip to the phone for its current pairing (ADR 0067 v6).
    /// nil when the phone is out of reach — the caller just keeps its error.
    func requestContext() async -> [String: String]? {
        let session = WCSession.default
        guard WCSession.isSupported(), session.activationState == .activated,
            session.isReachable
        else { return nil }
        return await withCheckedContinuation { continuation in
            let reply = BoundedContextReply(continuation)
            session.sendMessage(
                ["want": "pairing"],
                replyHandler: { response in
                    let typed = response.compactMapValues { $0 as? String }
                    reply.finish(typed.isEmpty ? nil : typed)
                },
                errorHandler: { _ in reply.finish(nil) })
            DispatchQueue.global().asyncAfter(deadline: .now() + 5) { reply.finish(nil) }
        }
    }
}

private final class BoundedContextReply: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<[String: String]?, Never>?

    init(_ continuation: CheckedContinuation<[String: String]?, Never>) {
        self.continuation = continuation
    }

    func finish(_ context: [String: String]?) {
        lock.withLock {
            continuation?.resume(returning: context)
            continuation = nil
        }
    }
}
