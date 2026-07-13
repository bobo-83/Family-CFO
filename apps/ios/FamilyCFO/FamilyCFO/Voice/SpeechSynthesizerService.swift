import AVFoundation
import Foundation

/// Speaks replies out loud. M86 uses the system voice (works offline, zero
/// infrastructure); the on-box Kokoro stream (M87) will slot in behind the
/// same protocol.
@MainActor
protocol SpeechSynthesizing: AnyObject {
    /// Speaks the text; returns when speech finishes or is stopped.
    func speak(_ text: String) async
    func stopSpeaking()
}

@MainActor
final class SpeechSynthesizerService: NSObject, SpeechSynthesizing, AVSpeechSynthesizerDelegate {
    private let synthesizer = AVSpeechSynthesizer()
    private var finishContinuation: CheckedContinuation<Void, Never>?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    func speak(_ text: String) async {
        stopSpeaking()
        guard !text.isEmpty else { return }
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = Self.bestAvailableVoice()
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            finishContinuation = continuation
            synthesizer.speak(utterance)
        }
    }

    /// The default voice is the "compact" (most robotic) tier. Prefer the
    /// highest-quality voice the user has installed for their language —
    /// premium > enhanced > default. Users can download premium voices in
    /// Settings → Accessibility → Spoken Content → Voices.
    static func bestAvailableVoice() -> AVSpeechSynthesisVoice? {
        let language = AVSpeechSynthesisVoice.currentLanguageCode()
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
