import Foundation
import Testing

@testable import FamilyCFO

@MainActor
final class MockSpeechEngine: SpeechEngine {
    var permissionGranted = true
    var startError: Error?
    private(set) var startCount = 0
    private(set) var stopCount = 0
    private var continuation: AsyncStream<String>.Continuation?

    func requestPermission() async -> Bool { permissionGranted }

    func startTranscribing() async throws -> AsyncStream<String> {
        if let startError { throw startError }
        startCount += 1
        let (stream, continuation) = AsyncStream<String>.makeStream()
        self.continuation = continuation
        return stream
    }

    func stopTranscribing() {
        stopCount += 1
        continuation?.finish()
        continuation = nil
    }

    func hear(_ text: String) {
        continuation?.yield(text)
    }

    /// Simulated microphone energy; tests bump this to say "still talking".
    var lastVoiceActivity: ContinuousClock.Instant?
}

@MainActor
final class MockSynthesizer: SpeechSynthesizing {
    private(set) var spoken: [String] = []
    /// The language each utterance was spoken in (#10 phase 1).
    private(set) var languages: [String?] = []
    private(set) var stopCount = 0

    func speak(_ text: String, language: String?) async {
        spoken.append(text)
        languages.append(language)
    }

    func stopSpeaking() {
        stopCount += 1
    }
}

/// Speaks "forever" until stopSpeaking is called — lets tests hold the
/// session in the .speaking phase.
@MainActor
final class BlockingMockSynthesizer: SpeechSynthesizing {
    private var continuation: CheckedContinuation<Void, Never>?

    func speak(_ text: String, language: String?) async {
        await withCheckedContinuation { continuation = $0 }
    }

    func stopSpeaking() {
        continuation?.resume()
        continuation = nil
    }
}

@MainActor
struct VoiceSessionViewModelTests {
    private func makeModel(
        api: MockAdvisorAPI = MockAdvisorAPI()
    ) -> (VoiceSessionViewModel, MockSpeechEngine, MockSynthesizer, MockAdvisorAPI) {
        let engine = MockSpeechEngine()
        let synth = MockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: nil, engine: engine, synthesizer: synth)
        return (model, engine, synth, api)
    }

    private func groundedResponse(_ answer: String) -> Components.Schemas.ChatResponse {
        .init(
            conversationId: "conv-voice",
            recommendation: .init(
                id: "rec-1",
                answer: answer,
                assumptions: [],
                impacts: [],
                tradeoffs: [],
                alternatives: [],
                confidence: 0.8,
                calculationRefs: []
            )
        )
    }

    @Test func deniedPermissionSurfacesState() async {
        let (model, engine, _, _) = makeModel()
        engine.permissionGranted = false

        await model.begin()

        #expect(model.phase == .denied)
    }

    @Test func utteranceGoesThroughTheGroundedPipelineAndIsSpoken() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("You have **4.2 months** of runway.")
        let (model, engine, synth, _) = makeModel(api: api)

        await model.begin()
        #expect(model.phase == .listening)
        engine.hear("how long is our runway")
        // Let the listen task consume the yield (bounded, deterministic).
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        #expect(model.transcript == "how long is our runway")
        await model.sendCurrentUtterance()

        #expect(api.sentMessages.count == 1)
        #expect(api.sentMessages[0].message == "how long is our runway")
        #expect(api.sentMessages[0].attachment == nil)
        // Markdown is stripped before speaking.
        #expect(synth.spoken == ["You have 4.2 months of runway."])
        #expect(model.conversationID == "conv-voice")
        // Hands-free: back to listening after the answer.
        #expect(model.phase == .listening)
        #expect(engine.startCount == 2)
    }

    /// An empty or unspeakable answer must never be silent dead air (user
    /// report 2026-07-21) — the user is hands-free and would just hear nothing.
    @Test func unspeakableAnswerIsSpokenAsAnApology() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("")
        let (model, engine, synth, _) = makeModel(api: api)

        await model.begin()
        engine.hear("what about my social security")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        #expect(synth.spoken.count == 1)
        #expect(synth.spoken[0].contains("couldn't come up with an answer"))
        #expect(model.phase == .listening)
    }

    /// #10 phase 1: a Lithuanian answer in a Lithuanian household speaks with
    /// the Lithuanian voice — detection and the household fallback agree.
    @Test func answersAreSpokenInTheHouseholdLanguage() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("Jūsų santaupos auga.")
        let engine = MockSpeechEngine()
        let synth = MockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: nil, engine: engine, synthesizer: synth,
            language: { "lt" })

        await model.begin()
        engine.hear("kaip mano santaupos")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        #expect(synth.spoken == ["Jūsų santaupos auga."])
        #expect(synth.languages == ["lt"])
    }

    /// The voice follows the language of the TEXT: a Vietnamese answer speaks
    /// Vietnamese even while the household setting says English — an answer
    /// can predate a language switch (user report, 2026-08-07).
    @Test func vietnameseAnswerSpeaksVietnameseEvenInAnEnglishHousehold() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("Giá trị tài sản ròng của bạn đang tăng lên trong tháng này.")
        let engine = MockSpeechEngine()
        let synth = MockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: nil, engine: engine, synthesizer: synth,
            language: { "en" })

        await model.begin()
        engine.hear("tiền của tôi thế nào")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        #expect(synth.languages == ["vi"])
    }

    /// The canned "no answer" apology must follow the household language too —
    /// an English sentence read by a Vietnamese voice is the pronunciation bug
    /// inverted.
    @Test func unspeakableAnswerApologizesInTheHouseholdLanguage() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("")
        let engine = MockSpeechEngine()
        let synth = MockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: nil, engine: engine, synthesizer: synth,
            language: { "vi" })

        await model.begin()
        engine.hear("tiền của tôi thế nào")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        #expect(synth.spoken == ["Xin lỗi, tôi chưa nghĩ ra câu trả lời. Bạn thử hỏi lại nhé."])
        #expect(synth.languages == ["vi"])
        #expect(model.phase == .listening)
    }

    /// Regression (user report 2026-07-21): a long grounded answer outlasted
    /// the HTTP connection and voice died with "couldn't reach the server" —
    /// but the box finished and SAVED the answer. Voice must recover it the
    /// same way the text chat does.
    @Test func aDroppedConnectionRecoversTheSavedAnswerAndSpeaksIt() async {
        let api = MockAdvisorAPI()
        api.sendError = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost)
        api.detail = .init(
            id: "conv-voice",
            title: "Retirement",
            createdAt: Date(),
            updatedAt: Date(),
            messages: [
                .init(
                    id: "m1", role: .user, content: "I'm using it with my 401k",
                    sequence: 1, createdAt: .now),
                .init(
                    id: "m2", role: .assistant, content: "Then it changes the outlook.",
                    sequence: 2, createdAt: .now),
            ]
        )
        let engine = MockSpeechEngine()
        let synth = MockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: "conv-voice", engine: engine, synthesizer: synth)

        await model.begin()
        engine.hear("I'm using it with my 401k")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        #expect(synth.spoken == ["Then it changes the outlook."])
        #expect(model.lastAnswer == "Then it changes the outlook.")
        #expect(model.phase == .listening)
    }

    @Test func emptyTranscriptIsNeverSent() async {
        let (model, _, _, api) = makeModel()

        await model.begin()
        await model.sendCurrentUtterance()

        #expect(api.sentMessages.isEmpty)
        #expect(model.phase == .listening)
    }

    @Test func apiFailureLandsInFailedState() async {
        let api = MockAdvisorAPI()
        api.error = APIError.server(500)
        let (model, engine, _, _) = makeModel(api: api)

        await model.begin()
        engine.hear("hello")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        await model.sendCurrentUtterance()

        if case .failed = model.phase {
        } else {
            Issue.record("expected .failed, got \(model.phase)")
        }
    }

    @Test func interruptRestartsListeningExactlyOnce() async {
        // Regression: interrupting used to restart listening from two code
        // paths at once, installing two microphone taps (Core Audio crash:
        // "required condition is false: nullptr == Tap()").
        let api = MockAdvisorAPI()
        api.response = groundedResponse("A very long answer worth interrupting.")
        let engine = MockSpeechEngine()
        let synth = BlockingMockSynthesizer()
        let model = VoiceSessionViewModel(
            api: api, conversationID: nil, engine: engine, synthesizer: synth)

        await model.begin()
        engine.hear("tell me everything")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        let sendTask = Task { await model.sendCurrentUtterance() }
        for _ in 0..<1000 where model.phase != .speaking { await Task.yield() }
        #expect(model.phase == .speaking)

        model.interruptSpeech()
        await sendTask.value

        #expect(model.phase == .listening)
        // begin() + exactly one restart — not two.
        #expect(engine.startCount == 2)
    }

    /// Regression (user report 2026-07-21): the recognizer's partial results
    /// stall mid-word on long utterances, and transcript changes were the only
    /// "still talking" signal — so a long question was auto-sent cut off
    /// ("…can I rely on that when I ret"). Microphone energy must hold the
    /// turn open while the hypothesis is being revised.
    @Test func voiceActivityHoldsTheSendWhileTheTranscriptStalls() async {
        let api = MockAdvisorAPI()
        api.response = groundedResponse("Answer.")
        let (model, engine, _, _) = makeModel(api: api)
        model.endOfUtterance = EndOfUtterance(
            settled: .milliseconds(40),
            unsettled: .milliseconds(40),
            hangingClause: .milliseconds(40)
        )
        // A starved CI runner can stall this test past the 10s noise-escape
        // hatch, which then sends BY DESIGN and fails the hold assertion —
        // the hatch is product behavior, the stall is not.
        model.noiseEscapeGrace = .seconds(600)

        await model.begin()
        // "Still talking" is pinned BEFORE the transcript arrives: on a
        // starved CI runner the 40ms settle timers can fire inside the gap
        // between hear() and this assignment, auto-sending early and failing
        // the hold assertion (observed 2026-08-07 once NL model loading in
        // parallel suites added CPU pressure).
        engine.lastVoiceActivity = .now + .seconds(60)
        engine.hear("what about my social security can I rely on that when I ret")
        for _ in 0..<1000 where model.transcript.isEmpty { await Task.yield() }
        try? await Task.sleep(for: .milliseconds(250))
        #expect(api.sentMessages.isEmpty)

        // The user actually stops; the required silence then elapses.
        engine.lastVoiceActivity = .now - .seconds(60)
        for _ in 0..<300 {
            if !api.sentMessages.isEmpty { break }
            try? await Task.sleep(for: .milliseconds(10))
        }
        #expect(api.sentMessages.count == 1)
        #expect(
            api.sentMessages[0].message
                == "what about my social security can I rely on that when I ret")
    }

    @Test func silenceTriggeredAutoSendDoesNotCancelItsOwnRequest() async {
        // Regression: the silence watcher used to run sendCurrentUtterance
        // inside its own task and then cancel that task as cleanup —
        // aborting the in-flight chat request with Swift.CancellationError.
        let api = MockAdvisorAPI()
        api.response = groundedResponse("All good.")
        let (model, engine, synth, _) = {
            let engine = MockSpeechEngine()
            let synth = MockSynthesizer()
            let model = VoiceSessionViewModel(
                api: api, conversationID: nil, engine: engine, synthesizer: synth)
            return (model, engine, synth, api)
        }()
        model.endOfUtterance = EndOfUtterance(
            settled: .milliseconds(1),
            unsettled: .milliseconds(1),
            hangingClause: .milliseconds(1)
        )

        await model.begin()
        engine.hear("are we on track")
        // Wait for the real silence watcher to fire the send (bounded).
        for _ in 0..<300 {
            if !api.sentMessages.isEmpty, model.phase == .listening { break }
            try? await Task.sleep(for: .milliseconds(10))
        }

        #expect(api.sentMessages.count == 1)
        #expect(synth.spoken == ["All good."])
        // No .failed(CancellationError); the loop resumed listening.
        #expect(model.phase == .listening)
    }

    @Test func endStopsEverything() async {
        let (model, engine, synth, _) = makeModel()

        await model.begin()
        model.end()

        #expect(model.phase == .idle)
        #expect(engine.stopCount >= 1)
        #expect(synth.stopCount >= 1)
        #expect(model.transcript.isEmpty)
    }
}
