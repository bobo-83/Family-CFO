import AVFoundation
import Foundation

// SpeechAudioAPI + LiveSpeechAudioAPI live in FamilyCFOShared (watch parity).

/// Speech that can fail. The natural voice depends on an optional service, so
/// "couldn't say it" is an expected outcome the caller must handle — unlike
/// `SpeechSynthesizing`, which always speaks.
@MainActor
protocol ThrowingSpeechSynthesizing: AnyObject {
    func speak(_ text: String) async throws
    func stopSpeaking()
}

/// Thrown when the on-box voice spoke part of an answer before failing, so the
/// fallback resumes exactly where it left off instead of repeating sentences
/// the user already heard.
struct RemainingSpeech: Error {
    let text: String
    let underlying: Error
}

/// Plays one chunk of MP3 and returns when it finishes. A seam because the
/// chunking, interrupt and fallback logic above it is the part worth testing,
/// and a test host has no audio route to play through.
@MainActor
protocol AudioChunkPlaying: AnyObject {
    func play(_ audio: Data) async throws
    func stop()
}

/// Deliberately does NOT set an `AVAudioSession` category. During a hands-free
/// conversation `VoiceAudioSession` owns the session (`.playAndRecord`, so the
/// reply plays out through the same session the microphone is on);
/// reconfiguring it here would tear down the mic that tap-to-interrupt — and
/// future acoustic barge-in — depend on.
@MainActor
final class AVAudioPlayerChunkPlayer: NSObject, AudioChunkPlaying, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?
    private var finishContinuation: CheckedContinuation<Void, Never>?
    private var isStopped = false

    func play(_ audio: Data) async throws {
        isStopped = false
        let player = try AVAudioPlayer(data: audio)
        player.delegate = self
        self.player = player
        guard player.play() else { throw AudioPlaybackError.couldNotStart }
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            // stop() may already have fired: resume at once rather than parking
            // a continuation nothing will ever wake.
            if isStopped {
                continuation.resume()
            } else {
                finishContinuation = continuation
            }
        }
        if isStopped { throw CancellationError() }
    }

    func stop() {
        isStopped = true
        player?.stop()
        player = nil
        resumeFinish()
    }

    private func resumeFinish() {
        finishContinuation?.resume()
        finishContinuation = nil
    }

    nonisolated func audioPlayerDidFinishPlaying(
        _ player: AVAudioPlayer, successfully flag: Bool
    ) {
        Task { @MainActor in self.resumeFinish() }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(
        _ player: AVAudioPlayer, error: Error?
    ) {
        Task { @MainActor in self.resumeFinish() }
    }
}

/// The natural on-box voice (M87): Kokoro-82M synthesized on the box, played
/// here sentence by sentence.
@MainActor
final class KokoroSpeechSynthesizer: ThrowingSpeechSynthesizing {
    private let api: SpeechAudioAPI
    private let player: AudioChunkPlaying
    private var pendingFetch: Task<Data, Error>?
    private var isStopped = false

    /// The player is injected only by tests; a default argument can't build one
    /// here because default expressions are evaluated off the main actor.
    init(api: SpeechAudioAPI, player: AudioChunkPlaying? = nil) {
        self.api = api
        self.player = player ?? AVAudioPlayerChunkPlayer()
    }

    func speak(_ text: String) async throws {
        isStopped = false
        let chunks = SpokenReply.sentences(text)
        guard !chunks.isEmpty else { return }

        var fetch = fetchTask(for: chunks[0])
        for index in chunks.indices {
            let audio: Data
            do {
                audio = try await fetch.value
            } catch {
                throw stopWithRemainder(chunks[index...], underlying: error)
            }
            // Kick off the NEXT sentence's synthesis before playing this one:
            // that overlap is what keeps the seams between sentences inaudible.
            if index + 1 < chunks.count {
                fetch = fetchTask(for: chunks[index + 1])
            }
            guard !isStopped else { throw CancellationError() }
            // A chunk that synthesized to nothing (a divider or punctuation the
            // text cleaner missed) must not derail the on-box voice — skip it and
            // keep going rather than throwing and dropping to the system voice.
            if audio.isEmpty { continue }
            do {
                try await player.play(audio)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                throw stopWithRemainder(chunks[index...], underlying: error)
            }
            guard !isStopped else { throw CancellationError() }
        }
        pendingFetch = nil
    }

    func stopSpeaking() {
        isStopped = true
        pendingFetch?.cancel()
        pendingFetch = nil
        player.stop()
    }

    private func fetchTask(for chunk: String) -> Task<Data, Error> {
        let task = Task { [api] in try await api.synthesize(chunk) }
        pendingFetch = task
        return task
    }

    /// Abandons the in-flight synthesis and reports what never got spoken, so
    /// the fallback resumes rather than repeats.
    private func stopWithRemainder(
        _ remaining: ArraySlice<String>, underlying: Error
    ) -> RemainingSpeech {
        pendingFetch?.cancel()
        pendingFetch = nil
        return RemainingSpeech(text: remaining.joined(separator: " "), underlying: underlying)
    }
}

/// The natural voice with the system voice underneath it (M87). Falling back is
/// a normal path — the `tts` service is optional and answers 503 when absent —
/// so it lives here structurally rather than as a branch inside the voice
/// session, which stays unaware that there is more than one voice.
@MainActor
final class FallbackSpeechSynthesizer: SpeechSynthesizing {
    private let primary: ThrowingSpeechSynthesizing
    private let fallback: SpeechSynthesizing
    private var isStopped = false

    init(primary: ThrowingSpeechSynthesizing, fallback: SpeechSynthesizing) {
        self.primary = primary
        self.fallback = fallback
    }

    func speak(_ text: String, language: String?) async {
        isStopped = false
        // Kokoro is an English-only model — it reads Vietnamese or Lithuanian
        // text with English phonetics (user report, 2026-07-26). Non-English
        // goes straight to the system voice. Decided here, per utterance, NOT
        // at construction: the synthesizer is built once at configure time
        // while the household language can change at any moment in Settings.
        guard language == nil || language == "en" else {
            await fallback.speak(text, language: language)
            return
        }
        do {
            try await primary.speak(text)
        } catch is CancellationError {
            // The user interrupted. Silence is the point — do NOT resurrect the
            // answer in the system voice.
        } catch let remaining as RemainingSpeech {
            guard !isStopped else { return }
            await fallback.speak(remaining.text, language: language)
        } catch {
            guard !isStopped else { return }
            await fallback.speak(text, language: language)
        }
    }

    func stopSpeaking() {
        isStopped = true
        primary.stopSpeaking()
        fallback.stopSpeaking()
    }
}

enum SpeechSynthesizerFactory {
    /// The on-box Kokoro voice when the app is paired to a box, with the system
    /// voice underneath; the system voice alone when it isn't.
    @MainActor
    static func make(speechAudio: SpeechAudioAPI?) -> SpeechSynthesizing {
        let system = SpeechSynthesizerService()
        guard let speechAudio else { return system }
        return FallbackSpeechSynthesizer(
            primary: KokoroSpeechSynthesizer(api: speechAudio),
            fallback: system
        )
    }
}
