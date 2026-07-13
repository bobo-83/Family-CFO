import Foundation
import Testing

@testable import FamilyCFO

/// Scripted `/voice/tts`. `failures` maps a sentence to the error it should
/// raise, so a mid-answer failure can be provoked precisely.
@MainActor
final class MockSpeechAudioAPI: SpeechAudioAPI, @unchecked Sendable {
    var failures: [String: Error] = [:]
    var globalError: Error?
    private(set) var requested: [String] = []

    nonisolated func synthesize(_ text: String) async throws -> Data {
        try await MainActor.run {
            requested.append(text)
            if let globalError { throw globalError }
            if let failure = failures[text] { throw failure }
            return Data("mp3:\(text)".utf8)
        }
    }
}

@MainActor
final class StubChunkPlayer: AudioChunkPlaying {
    private(set) var played: [String] = []
    var onPlay: (() -> Void)?

    func play(_ audio: Data) async throws {
        played.append(String(decoding: audio, as: UTF8.self))
        onPlay?()
    }

    func stop() {}
}

@MainActor
final class RecordingSystemSynthesizer: SpeechSynthesizing {
    private(set) var spoken: [String] = []

    func speak(_ text: String) async {
        spoken.append(text)
    }

    func stopSpeaking() {}
}

/// A primary voice scripted to fail in a specific way, so the composer's
/// fallback rules can be tested without any audio at all.
@MainActor
final class StubThrowingSynthesizer: ThrowingSpeechSynthesizing {
    var error: Error?
    private(set) var spoken: [String] = []
    private(set) var stopCount = 0

    func speak(_ text: String) async throws {
        spoken.append(text)
        if let error { throw error }
    }

    func stopSpeaking() { stopCount += 1 }
}

@MainActor
struct KokoroSpeechSynthesizerTests {
    @Test func speaksEachSentenceInOrder() async throws {
        let api = MockSpeechAudioAPI()
        let player = StubChunkPlayer()
        let kokoro = KokoroSpeechSynthesizer(api: api, player: player)

        try await kokoro.speak("Net worth is up. Bills look fine.")

        #expect(api.requested == ["Net worth is up.", "Bills look fine."])
        #expect(player.played == ["mp3:Net worth is up.", "mp3:Bills look fine."])
    }

    /// The 503 the API returns when the optional `tts` service isn't deployed.
    /// Nothing was spoken, so the whole answer must come back for the fallback.
    @Test func serviceUnavailableSurrendersTheWholeAnswer() async throws {
        let api = MockSpeechAudioAPI()
        api.globalError = SpeechAudioError.unavailable
        let kokoro = KokoroSpeechSynthesizer(api: api, player: StubChunkPlayer())

        await #expect(throws: RemainingSpeech.self) {
            try await kokoro.speak("First sentence. Second sentence.")
        }

        do {
            try await kokoro.speak("First sentence. Second sentence.")
            Issue.record("expected the on-box voice to give up")
        } catch let remaining as RemainingSpeech {
            #expect(remaining.text == "First sentence. Second sentence.")
            #expect(remaining.underlying as? SpeechAudioError == .unavailable)
        }
    }

    /// The reason `RemainingSpeech` carries text at all: when the service dies
    /// halfway, the fallback must pick up where it stopped rather than replay
    /// sentences the user already heard in the natural voice.
    @Test func midAnswerFailureSurrendersOnlyWhatWasNotSpoken() async throws {
        let api = MockSpeechAudioAPI()
        api.failures["Second sentence."] = SpeechAudioError.server(500)
        let player = StubChunkPlayer()
        let kokoro = KokoroSpeechSynthesizer(api: api, player: player)

        do {
            try await kokoro.speak("First sentence. Second sentence. Third sentence.")
            Issue.record("expected the on-box voice to give up mid-answer")
        } catch let remaining as RemainingSpeech {
            #expect(player.played == ["mp3:First sentence."])
            #expect(remaining.text == "Second sentence. Third sentence.")
        }
    }

    /// Tap-to-interrupt: stopping mid-answer is a cancellation, NOT a failure —
    /// it must not look like something the fallback should rescue.
    @Test func interruptingReportsCancellationNotFailure() async throws {
        let api = MockSpeechAudioAPI()
        let player = StubChunkPlayer()
        let kokoro = KokoroSpeechSynthesizer(api: api, player: player)
        player.onPlay = { kokoro.stopSpeaking() }

        await #expect(throws: CancellationError.self) {
            try await kokoro.speak("First sentence. Second sentence.")
        }
        #expect(player.played == ["mp3:First sentence."])
    }
}

@MainActor
struct FallbackSpeechSynthesizerTests {
    @Test func silentWhenTheNaturalVoiceSucceeds() async {
        let primary = StubThrowingSynthesizer()
        let system = RecordingSystemSynthesizer()
        let synthesizer = FallbackSpeechSynthesizer(primary: primary, fallback: system)

        await synthesizer.speak("All good.")

        #expect(primary.spoken == ["All good."])
        #expect(system.spoken.isEmpty)
    }

    /// The `tts` service is optional by design, so its absence must degrade to
    /// the system voice — the user still hears the answer.
    @Test func fallsBackToTheSystemVoiceWhenTheServiceIsAbsent() async {
        let primary = StubThrowingSynthesizer()
        primary.error = SpeechAudioError.unavailable
        let system = RecordingSystemSynthesizer()
        let synthesizer = FallbackSpeechSynthesizer(primary: primary, fallback: system)

        await synthesizer.speak("Your net worth is up.")

        #expect(system.spoken == ["Your net worth is up."])
    }

    @Test func fallbackResumesInsteadOfRepeating() async {
        let primary = StubThrowingSynthesizer()
        primary.error = RemainingSpeech(
            text: "Second sentence.", underlying: SpeechAudioError.server(500))
        let system = RecordingSystemSynthesizer()
        let synthesizer = FallbackSpeechSynthesizer(primary: primary, fallback: system)

        await synthesizer.speak("First sentence. Second sentence.")

        // Only the unspoken remainder — not the sentence Kokoro already said.
        #expect(system.spoken == ["Second sentence."])
    }

    /// The bug this guards: interrupting the natural voice and then hearing the
    /// robot voice start reading the same answer back.
    @Test func interruptingDoesNotResurrectTheAnswerInTheSystemVoice() async {
        let primary = StubThrowingSynthesizer()
        primary.error = CancellationError()
        let system = RecordingSystemSynthesizer()
        let synthesizer = FallbackSpeechSynthesizer(primary: primary, fallback: system)

        await synthesizer.speak("A long answer the user cut off.")

        #expect(system.spoken.isEmpty)
    }

    @Test func stoppingSilencesBothVoices() async {
        let primary = StubThrowingSynthesizer()
        let system = RecordingSystemSynthesizer()
        let synthesizer = FallbackSpeechSynthesizer(primary: primary, fallback: system)

        synthesizer.stopSpeaking()

        #expect(primary.stopCount == 1)
    }
}
