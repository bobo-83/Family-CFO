import AVFoundation
import SwiftUI

/// Reads a single advisor answer aloud on demand (the transcript's "Read aloud"
/// button), reusing the same on-box Kokoro voice + system-voice fallback as the
/// hands-free voice session. Tracks which message is speaking so the button can
/// show playing state and toggle off. Markdown is stripped to speakable text.
@MainActor
@Observable
final class ReadAloudController {
    private var synthesizer: SpeechSynthesizing?
    private(set) var speakingMessageID: String?
    private var task: Task<Void, Never>?

    /// Build the synthesizer once, from the paired box's audio API (Kokoro) with
    /// the system voice underneath — or the system voice alone when unpaired.
    func configure(speechAudio: SpeechAudioAPI?) {
        guard synthesizer == nil else { return }
        synthesizer = SpeechSynthesizerFactory.make(speechAudio: speechAudio)
    }

    func isSpeaking(_ messageID: String) -> Bool { speakingMessageID == messageID }

    /// Tap to start reading this message; tap again (or tap another) to stop.
    func toggle(messageID: String, markdown: String) {
        guard let synthesizer else { return }
        if speakingMessageID == messageID {
            stop()
            return
        }
        stop()
        activateAudioSession()
        speakingMessageID = messageID
        task = Task { [weak self] in
            await synthesizer.speak(SpokenReply.speakable(markdown))
            guard let self, self.speakingMessageID == messageID else { return }
            self.speakingMessageID = nil
        }
    }

    func stop() {
        task?.cancel()
        task = nil
        synthesizer?.stopSpeaking()
        speakingMessageID = nil
    }

    /// Play through the speaker as media — audible even with the ring/silent
    /// switch on, matching how a podcast or the read-aloud in Books behaves.
    private func activateAudioSession() {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio)
        try? session.setActive(true)
    }
}
