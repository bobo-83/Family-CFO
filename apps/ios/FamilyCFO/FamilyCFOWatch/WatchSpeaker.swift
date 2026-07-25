import AVFoundation
import Foundation
import Observation

/// Speaks advisor answers on the wrist: the box's natural voice when the
/// optional tts service is up (same `POST /voice/tts` every client uses),
/// the system voice otherwise — the answer is never silent (ADR 0058 spirit).
///
/// Observable state machine (user report 2026-07-25: synthesis has a real
/// delay, and silent taps piled up OVERLAPPING streams): exactly one
/// utterance can be in flight; a new request supersedes the old one, and the
/// per-bubble buttons render loading/speaking from `phase` + `currentTag`.
@MainActor
@Observable
final class WatchSpeaker: NSObject, AVAudioPlayerDelegate, AVSpeechSynthesizerDelegate {
    enum Phase { case idle, loading, speaking }

    private(set) var phase: Phase = .idle
    /// The chat bubble index sounding right now (nil for auto-spoken answers).
    private(set) var currentTag: Int?

    private var player: AVAudioPlayer?
    private let fallback = AVSpeechSynthesizer()
    private var finished: CheckedContinuation<Void, Never>?
    /// Bumped by every speak/stop: a synthesis that returns to a stale
    /// generation was cancelled or superseded and must discard its audio.
    private var generation = 0

    var isSpeaking: Bool { phase != .idle }

    override init() {
        super.init()
        fallback.delegate = self
    }

    func speak(_ text: String, api: SpeechAudioAPI?, tag: Int? = nil) async {
        stop()
        let spoken = SpokenReply.speakable(text)
        guard !spoken.isEmpty else { return }
        generation += 1
        let mine = generation
        phase = .loading
        currentTag = tag
        // Long-form audio is the ONLY way playback survives the wrist going
        // down on watchOS — a default session dies with the suspended app,
        // which cut answers off mid-sentence (user report 2026-07-25). The
        // async activation may ask once where to play (speaker or AirPods).
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio, policy: .longFormAudio)
        _ = try? await session.activate()
        guard mine == generation else { return }
        if let api, let data = try? await api.synthesize(spoken) {
            guard mine == generation else { return }
            if let player = try? AVAudioPlayer(data: data) {
                self.player = player
                player.delegate = self
                if player.play() {
                    phase = .speaking
                    await withCheckedContinuation { finished = $0 }
                    return
                }
            }
        }
        guard mine == generation else { return }
        // System voice: on-device, always there.
        phase = .speaking
        let utterance = AVSpeechUtterance(string: spoken)
        utterance.prefersAssistiveTechnologySettings = false
        fallback.speak(utterance)
    }

    func stop() {
        generation += 1
        player?.stop()
        player = nil
        fallback.stopSpeaking(at: .immediate)
        finished?.resume()
        finished = nil
        phase = .idle
        currentTag = nil
    }

    private func didFinish() {
        finished?.resume()
        finished = nil
        phase = .idle
        currentTag = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in self.didFinish() }
    }

    nonisolated func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance
    ) {
        Task { @MainActor in self.didFinish() }
    }
}
