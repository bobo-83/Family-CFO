import Foundation
import WatchConnectivity

/// Pushes the paired server + credential to the Apple Watch (M-watch,
/// ADR 0067) so the watch talks to the box directly. Application context is
/// the right channel: latest-wins, delivered even when the watch app is
/// closed, and re-sent automatically after reinstalls. Signing out pushes an
/// empty token so the watch locks itself.
final class PhoneWatchBridge: NSObject, WCSessionDelegate, @unchecked Sendable {
    static let shared = PhoneWatchBridge()

    private var pending: [String: String]?

    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
    }

    /// Push (or clear, with nils) the watch's working set.
    func push(server: ServerConfig?, credential: StoredCredential?) {
        let context: [String: String] = [
            "apiBaseURL": server?.apiBaseURL.absoluteString ?? "",
            "certificateSHA256": server?.certificateSHA256 ?? "",
            "token": credential?.accessToken ?? "",
            "householdName": server?.householdName ?? "",
        ]
        let session = WCSession.default
        guard WCSession.isSupported(), session.activationState == .activated else {
            pending = context
            return
        }
        try? session.updateApplicationContext(context)
    }

    func session(
        _ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        if activationState == .activated, let pending {
            try? session.updateApplicationContext(pending)
            self.pending = nil
        }
    }

    /// The watch hit a 401 and wants the current pairing NOW (ADR 0067 v6).
    /// Read persisted state directly — this can arrive in a background launch
    /// where nobody has unlocked the app, so the in-memory model may be cold.
    func session(
        _ session: WCSession, didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        guard message["want"] as? String == "pairing" else {
            replyHandler([:])
            return
        }
        replyHandler(Self.persistedPairingContext() ?? [:])
    }

    static func persistedPairingContext() -> [String: String]? {
        guard let data = UserDefaults.standard.data(forKey: AppModel.serverDefaultsKey),
            let server = try? JSONDecoder().decode(ServerConfig.self, from: data),
            let credentialData = KeychainStore.load(account: AppModel.credentialAccount),
            let credential = try? JSONDecoder().decode(StoredCredential.self, from: credentialData)
        else { return nil }
        return [
            "apiBaseURL": server.apiBaseURL.absoluteString,
            "certificateSHA256": server.certificateSHA256 ?? "",
            "token": credential.accessToken,
            "householdName": server.householdName,
        ]
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}
