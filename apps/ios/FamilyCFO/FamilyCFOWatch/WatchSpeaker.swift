import AVFoundation
import Foundation

/// Speaks advisor answers on the wrist: the box's natural voice when the
/// optional tts service is up (same `POST /voice/tts` every client uses),
/// the system voice otherwise — the answer is never silent (ADR 0058 spirit).
@MainActor
final class WatchSpeaker: NSObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private let fallback = AVSpeechSynthesizer()
    private var finished: CheckedContinuation<Void, Never>?

    var isSpeaking: Bool { player?.isPlaying == true || fallback.isSpeaking }

    func speak(_ text: String, api: SpeechAudioAPI?) async {
        stop()
        let spoken = SpokenReply.speakable(text)
        guard !spoken.isEmpty else { return }
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try? AVAudioSession.sharedInstance().setActive(true)
        if let api, let data = try? await api.synthesize(spoken),
            let player = try? AVAudioPlayer(data: data)
        {
            self.player = player
            player.delegate = self
            if player.play() {
                await withCheckedContinuation { finished = $0 }
                return
            }
        }
        // System voice: on-device, always there.
        let utterance = AVSpeechUtterance(string: spoken)
        utterance.prefersAssistiveTechnologySettings = false
        fallback.speak(utterance)
    }

    func stop() {
        player?.stop()
        player = nil
        fallback.stopSpeaking(at: .immediate)
        finished?.resume()
        finished = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.finished?.resume()
            self.finished = nil
        }
    }
}
