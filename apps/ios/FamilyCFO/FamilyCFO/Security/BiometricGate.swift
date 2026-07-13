import Foundation
import LocalAuthentication

/// Face ID (or Touch ID / passcode) gate in front of the app's UI.
/// Where no local authentication is available at all — fresh simulator,
/// no passcode — the gate opens rather than bricking the app; the spec's
/// acceptance criterion is "Face ID protects local app access *where
/// available*".
enum BiometricGate {
    static func authenticate() async -> Bool {
        let context = LAContext()
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            return true
        }
        do {
            return try await context.evaluatePolicy(
                .deviceOwnerAuthentication,
                localizedReason: "Unlock your household finances"
            )
        } catch {
            return false
        }
    }
}
