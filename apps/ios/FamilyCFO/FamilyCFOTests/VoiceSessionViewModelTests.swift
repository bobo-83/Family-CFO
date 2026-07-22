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
}

@MainActor
final class MockSynthesizer: SpeechSynthesizing {
    private(set) var spoken: [String] = []
    private(set) var stopCount = 0

    func speak(_ text: String) async {
        spoken.append(text)
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

    func speak(_ text: String) async {
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
