import Foundation
import Observation

/// The hands-free conversation loop (M86): listen → detect end of utterance
/// by silence → send the transcript through the unchanged grounded chat
/// pipeline → speak the answer → listen again. Voice is a skin over
/// `POST /chat/messages`; there is no second brain (ADR 0018).
@MainActor
@Observable
final class VoiceSessionViewModel: Identifiable {
    let id = UUID()

    enum Phase: Equatable {
        case idle
        case listening
        case thinking
        case speaking
        case denied
        case failed(String)
    }

    private(set) var phase: Phase = .idle
    private(set) var transcript = ""
    private(set) var lastAnswer: String?
    private(set) var conversationID: String?

    /// When a pause counts as the end of what the user was saying. Not a flat
    /// timer: a mid-sentence hesitation must not be mistaken for a finished
    /// question (see `EndOfUtterance`).
    var endOfUtterance = EndOfUtterance()

    private let api: AdvisorAPI
    private let engine: SpeechEngine
    private let synthesizer: SpeechSynthesizing
    private var listenTask: Task<Void, Never>?
    private var silenceTask: Task<Void, Never>?
    private var sendTask: Task<Void, Never>?
    private var lastTranscriptChange = ContinuousClock.now

    init(
        api: AdvisorAPI,
        conversationID: String? = nil,
        engine: SpeechEngine,
        synthesizer: SpeechSynthesizing
    ) {
        self.api = api
        self.conversationID = conversationID
        self.engine = engine
        self.synthesizer = synthesizer
    }

    func begin() async {
        guard phase == .idle else { return }
        guard await engine.requestPermission() else {
            phase = .denied
            return
        }
        await startListening()
    }

    func end() {
        listenTask?.cancel()
        silenceTask?.cancel()
        sendTask?.cancel()
        engine.stopTranscribing()
        synthesizer.stopSpeaking()
        phase = .idle
        transcript = ""
    }

    /// Tap-to-interrupt while the answer is being read out. Only stops the
    /// synthesizer: that resumes the `speak(_:)` await inside
    /// `sendCurrentUtterance`, whose continuation is the ONLY path back to
    /// listening — two callers restarting concurrently would install two
    /// microphone taps and crash Core Audio.
    func interruptSpeech() {
        guard phase == .speaking else { return }
        synthesizer.stopSpeaking()
    }

    private func startListening() async {
        // Never stack listening sessions — a second microphone tap crashes
        // Core Audio.
        guard phase != .listening else { return }
        transcript = ""
        lastTranscriptChange = .now
        do {
            let updates = try await engine.startTranscribing()
            phase = .listening
            listenTask = Task { [weak self] in
                for await text in updates {
                    guard let self, !Task.isCancelled else { return }
                    if text != self.transcript {
                        self.transcript = text
                        self.lastTranscriptChange = .now
                    }
                }
            }
            watchForSilence()
        } catch {
            phase = .failed(
                (error as? LocalizedError)?.errorDescription
                    ?? "Couldn't start listening.")
        }
    }

    private func watchForSilence() {
        silenceTask?.cancel()
        silenceTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(200))
                guard let self, self.phase == .listening else { return }
                let quietFor = ContinuousClock.now - self.lastTranscriptChange
                let required = self.endOfUtterance.requiredSilence(after: self.transcript)
                if !self.transcript.isEmpty, quietFor >= required {
                    // Hop to a fresh task: sendCurrentUtterance cancels the
                    // silence watcher as cleanup, and running it INSIDE the
                    // watcher would cancel the in-flight chat request
                    // (Swift.CancellationError).
                    self.sendTask = Task { await self.sendCurrentUtterance() }
                    return
                }
            }
        }
    }

    /// Sends whatever has been transcribed so far. Internal so tests can
    /// drive the pipeline without waiting out the silence timer.
    func sendCurrentUtterance() async {
        let utterance = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !utterance.isEmpty, phase == .listening else { return }
        listenTask?.cancel()
        silenceTask?.cancel()
        engine.stopTranscribing()
        phase = .thinking
        do {
            let response = try await api.sendMessage(
                utterance, conversationID: conversationID, attachment: nil)
            conversationID = response.conversationId
            let answer = response.recommendation.answer
            lastAnswer = answer
            phase = .speaking
            let speakable = SpokenReply.speakable(answer)
            // An unspeakable answer must never be silent dead air — the user
            // has no screen open to notice (user report, 2026-07-21).
            await synthesizer.speak(
                speakable.isEmpty
                    ? "Sorry, I couldn't come up with an answer to that. Try asking again."
                    : speakable)
            // Hands-free: keep the conversation going unless interrupted or
            // ended (both of which change phase out from under us).
            if phase == .speaking {
                await startListening()
            }
        } catch is CancellationError {
            // The session was ended mid-request; nothing to report.
        } catch {
            phase = .failed(ChatViewModel.describe(error))
        }
    }
}
