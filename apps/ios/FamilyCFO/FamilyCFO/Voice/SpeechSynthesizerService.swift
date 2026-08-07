import AVFoundation
import Foundation

/// Speaks replies out loud. M86 uses the system voice (works offline, zero
/// infrastructure); the on-box Kokoro stream (M87) will slot in behind the
/// same protocol.
@MainActor
protocol SpeechSynthesizing: AnyObject {
    /// Speaks the text; returns when speech finishes or is stopped.
    /// `language` is the household's advisor language ("en" | "vi" | "lt",
    /// #10 phase 1) so the voice matches the text; nil behaves like "en".
    func speak(_ text: String, language: String?) async
    func stopSpeaking()
}

extension SpeechSynthesizing {
    /// English/device-voice behavior, unchanged from before languages existed.
    func speak(_ text: String) async {
        await speak(text, language: nil)
    }
}

@MainActor
final class SpeechSynthesizerService: NSObject, SpeechSynthesizing, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var finishContinuation: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String, language: String?) async {
        stopSpeaking()
        guard !text.isEmpty else { return }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = Self.bestAvailableVoice(for: language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            finishContinuation = continuation
            synthesizer.speak(utterance)
        }
    }

    /// The voice locale for the household's advisor language. "en" (and nil)
    /// keep the device's own locale — today's behavior, in the regional accent
    /// the user chose for their phone.
    static func voiceLanguageCode(for householdLanguage: String?) -> String {
        switch householdLanguage {
        case "vi": return "vi-VN"
        case "lt": return "lt-LT"
        default: return AVSpeechSynthesisVoice.currentLanguageCode()
        }
    }

    /// The best voice for the household's language: a device-language voice
    /// reads Vietnamese or Lithuanian answers with English phonetics (user
    /// report, 2026-07-26). When the device has no voice for that language at
    /// all, a wrong-accent voice still beats silence.
    static func bestAvailableVoice(for householdLanguage: String? = nil) -> AVSpeechSynthesisVoice? {
        let language = voiceLanguageCode(for: householdLanguage)
        if let voice = installedVoice(for: language) { return voice }
        let deviceLanguage = AVSpeechSynthesisVoice.currentLanguageCode()
        guard language != deviceLanguage else { return nil }
        return installedVoice(for: deviceLanguage)
    }

    /// The default voice is the "compact" (most robotic) tier. Prefer the
    /// highest-quality voice the user has installed for the language —
    /// premium > enhanced > default. Users can download premium voices in
    /// Settings → Accessibility → Spoken Content → Voices.
    private static func installedVoice(for language: String) -> AVSpeechSynthesisVoice? {
        func rank(_ quality: AVSpeechSynthesisVoiceQuality) -> Int {
            switch quality {
            case .premium: return 3
            case .enhanced: return 2
            default: return 1
            }
        }
        let best = AVSpeechSynthesisVoice.speechVoices()
            .filter { $0.language == language }
            .max { rank($0.quality) < rank($1.quality) }
        return best ?? AVSpeechSynthesisVoice(language: language)
    }

    func stopSpeaking() {
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance
    ) {
        Task { @MainActor in
            self.finishContinuation?.resume()
            self.finishContinuation = nil
        }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance
    ) {
        Task { @MainActor in
            self.finishContinuation?.resume()
            self.finishContinuation = nil
        }
    }
}
