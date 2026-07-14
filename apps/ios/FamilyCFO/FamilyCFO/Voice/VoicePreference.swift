import Foundation

/// Which voice reads answers in the in-app hands-free conversation (M87c). Siri
/// and the web dashboard are unaffected — this is only the in-app voice loop.
enum VoicePreference: String, CaseIterable, Identifiable {
    /// The on-box Kokoro voice, falling back to the device voice when the box's
    /// tts service is unreachable. One voice everywhere, but needs the box.
    case natural
    /// Apple's on-device voice only — instant, works away from home, no box load.
    case device

    var id: String { rawValue }

    var title: String {
        switch self {
        case .natural: return "Natural (on-box)"
        case .device: return "Device voice"
        }
    }

    var detail: String {
        switch self {
        case .natural:
            return "The on-box natural voice, the same one the dashboard uses. Falls back to the device voice when the box isn't reachable."
        case .device:
            return "Apple's on-device voice — instant, works away from home, and doesn't use the box."
        }
    }

    static let storageKey = "voice.preference"
    static let `default`: VoicePreference = .natural

    /// The stored choice, defaulting to natural (the prior behavior).
    static var current: VoicePreference {
        UserDefaults.standard.string(forKey: storageKey)
            .flatMap(VoicePreference.init(rawValue:)) ?? .default
    }
}
