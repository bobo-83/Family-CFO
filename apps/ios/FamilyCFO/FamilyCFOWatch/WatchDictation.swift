import Foundation
import WatchKit

/// Programmatic dictation (M-watch v3): watchOS has no Speech framework, so
/// the system text-input controller IS the microphone. Presenting it
/// programmatically is what turns single questions into a conversation — the
/// app re-opens it after each spoken answer until the user cancels.
@MainActor
enum WatchDictation {
    /// Present the input UI (dictation-first) and return the utterance, or
    /// nil when the user cancelled or the controller isn't reachable.
    static func ask() async -> String? {
        await withCheckedContinuation { continuation in
            guard let controller = WKExtension.shared().visibleInterfaceController else {
                continuation.resume(returning: nil)
                return
            }
            controller.presentTextInputController(
                withSuggestions: nil, allowedInputMode: .plain
            ) { results in
                continuation.resume(returning: results?.first as? String)
            }
        }
    }
}
