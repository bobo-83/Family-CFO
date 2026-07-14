import Foundation
import Testing

@testable import FamilyCFO

@MainActor
struct VoicePreferenceTests {
    private struct StubSpeechAudio: SpeechAudioAPI {
        func synthesize(_ text: String) async throws -> Data { Data() }
    }

    @Test func naturalWithABoxUsesTheKokoroFallbackChain() {
        let synth = SpeechSynthesizerFactory.make(
            speechAudio: StubSpeechAudio(), preference: .natural)

        #expect(synth is FallbackSpeechSynthesizer)
    }

    /// Choosing the device voice must NOT use Kokoro, even when a box is paired.
    @Test func deviceVoiceIgnoresTheBox() {
        let synth = SpeechSynthesizerFactory.make(
            speechAudio: StubSpeechAudio(), preference: .device)

        #expect(synth is SpeechSynthesizerService)
    }

    @Test func naturalWithoutABoxFallsToTheDeviceVoice() {
        let synth = SpeechSynthesizerFactory.make(speechAudio: nil, preference: .natural)

        #expect(synth is SpeechSynthesizerService)
    }

    @Test func defaultIsNatural() {
        #expect(VoicePreference.default == .natural)
    }
}
