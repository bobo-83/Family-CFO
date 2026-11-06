import CryptoKit
import Foundation
import WatchConnectivity

/// Relays the phone's current session over WatchConnectivity. The generation
/// record survives relaunch and contains only ordering metadata, never a bearer.
final class PhoneWatchBridge: NSObject, WCSessionDelegate, @unchecked Sendable {
    static let shared = PhoneWatchBridge()
    private static let stateKey = "family-cfo.watch-context-order"
    private let lock = NSLock()
    private var pending: [String: String]?

    func activate() {
        guard WCSession.isSupported() else { return }
        let session = WCSession.default
        session.delegate = self
        session.activate()
    }

    func push(server: ServerConfig?, credential: StoredCredential?) {
        lock.withLock {
            let context = orderedContext(server: server, credential: credential)
            publish(context, via: WCSession.default)
        }
    }

    private func publish(_ context: [String: String], via session: WCSession) {
        pending = context
        guard WCSession.isSupported(), session.activationState == .activated else { return }
        do {
            try session.updateApplicationContext(context)
            pending = nil
        } catch {
            // Keep the latest context for the next activation or foreground push.
        }
    }

    func session(
        _ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState,
        error: Error?
    ) {
        lock.withLock {
            if activationState == .activated, let pending { publish(pending, via: session) }
        }
    }

    func session(
        _ session: WCSession, didReceiveMessage message: [String: Any],
        replyHandler: @escaping ([String: Any]) -> Void
    ) {
        guard message["want"] as? String == "pairing" else {
            replyHandler([:])
            return
        }
        let context = lock.withLock { persistedContext(via: session) }
        replyHandler(context)
    }

    private func persistedContext(via session: WCSession) -> [String: String] {
        let defaults = UserDefaults.standard
        let state = defaults.dictionary(forKey: Self.stateKey) as? [String: String]
        if state?["state"] == "invalidated" {
            return orderedContext(server: nil, credential: nil)
        }
        guard let data = defaults.data(forKey: AppModel.serverDefaultsKey),
            let server = try? JSONDecoder().decode(ServerConfig.self, from: data),
            let credentialData = KeychainStore.load(account: AppModel.credentialAccount),
            let credential = try? JSONDecoder().decode(StoredCredential.self, from: credentialData)
        else { return [:] } // unavailable is not authoritative logout
        if credential.expiresAt <= Date() {
            let context = orderedContext(server: nil, credential: nil)
            publish(context, via: session)
            return context
        }
        return orderedContext(server: server, credential: credential)
    }

    private func orderedContext(server: ServerConfig?, credential: StoredCredential?) -> [String: String] {
        let defaults = UserDefaults.standard
        let previous = defaults.dictionary(forKey: Self.stateKey) as? [String: String] ?? [:]
        let active = if let server, let credential, credential.expiresAt > Date() {
            (server, credential)
        } else { nil as (ServerConfig, StoredCredential)? }
        let state = active == nil ? "invalidated" : "active"
        let fingerprint = active.map { pair in
            // Hash the random bearer to identify foreground re-sends without
            // copying it from Keychain into preferences.
            SHA256.hash(data: Data(pair.1.accessToken.utf8)).description
        }
        var sequence = previous["sequence"].flatMap(UInt64.init) ?? 0
        if previous["state"] != state || previous["fingerprint"] != fingerprint {
            sequence += 1
        }
        var metadata = [
            "version": "1", "state": state,
            "scope": previous["scope"] ?? UUID().uuidString,
            "sequence": String(sequence),
        ]
        metadata["fingerprint"] = fingerprint
        defaults.set(metadata, forKey: Self.stateKey)
        metadata.removeValue(forKey: "fingerprint")
        if let (server, credential) = active {
            metadata["apiBaseURL"] = server.apiBaseURL.absoluteString
            metadata["certificateSHA256"] = server.certificateSHA256 ?? ""
            metadata["token"] = credential.accessToken
            metadata["householdName"] = server.householdName
            metadata["expiresAt"] = String(credential.expiresAt.timeIntervalSince1970)
        }
        return metadata
    }

    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}
