import Foundation
import Observation
import WatchConnectivity

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

    var isPaired: Bool { apiBaseURL != nil && token != nil }

    var client: Client? {
        guard let apiBaseURL, let token else { return nil }
        let captured = token
        return APIClientFactory.makeClient(
            baseURL: apiBaseURL,
            pinnedCertificateSHA256: certificateSHA256,
            token: { captured }
        )
    }

    var advisor: AdvisorAPI? { client.map { LiveAdvisorAPI(client: $0) } }
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
            let stored = try? JSONDecoder().decode([String: String].self, from: data)
        else { return }
        apply(stored, persist: false)
    }

    func apply(_ context: [String: String], persist: Bool = true) {
        apiBaseURL = context["apiBaseURL"].flatMap(URL.init(string:))
        // #86: the phone relays "" when the box's certificate is CA-signed and
        // nothing was pinned — application context carries strings, not
        // optionals. An empty pin is no pin; keeping it as "" pinned the watch
        // to a hash no certificate can have, so the watch alone refused every
        // request while the phone was happily connected.
        certificateSHA256 = CertificatePin.normalizedPin(context["certificateSHA256"])
        token = context["token"].flatMap { $0.isEmpty ? nil : $0 }
        householdName = context["householdName"]
        if persist, let data = try? JSONEncoder().encode(context) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
    }

    /// ADR 0067 v6: a 401 usually means the phone rotated the session while
    /// our pushed copy lagged (user report 2026-07-25 — "the box answered
    /// unexpectedly" right after an update, gone once the phone was opened).
    /// Ask the phone for its CURRENT pairing over the live channel — waking
    /// its app in the background if needed — and apply the reply. Returns
    /// true when a different token landed, i.e. a retry is worth it.
    func requestFreshCredential() async -> Bool {
        let stale = token
        guard let context = await connectivity.requestContext() else { return false }
        apply(context)
        return token != nil && token != stale
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
            session.sendMessage(
                ["want": "pairing"],
                replyHandler: { reply in
                    let typed = reply.compactMapValues { $0 as? String }
                    continuation.resume(returning: typed.isEmpty ? nil : typed)
                },
                errorHandler: { _ in continuation.resume(returning: nil) })
        }
    }
}
