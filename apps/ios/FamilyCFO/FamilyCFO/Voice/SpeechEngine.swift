import AVFoundation
import Foundation

/// On-device speech-to-text behind a seam (M86, ADR 0018). Both
/// implementations transcribe entirely on the phone — raw audio NEVER
/// leaves the device; only the finished transcript is sent to the chat
/// endpoint.
@MainActor
protocol SpeechEngine: AnyObject {
    /// Ask for microphone + speech permissions. False means the user
    /// declined (now or previously) and the caller should point at Settings.
    func requestPermission() async -> Bool

    /// Start listening. Yields the cumulative transcript of the current
    /// utterance after every recognition update; the stream finishes when
    /// `stopTranscribing()` is called or recognition ends.
    func startTranscribing() async throws -> AsyncStream<String>

    func stopTranscribing()

    /// When the microphone last heard voice-loud audio, or nil if it hasn't
    /// yet. The end-of-utterance detector treats this as proof the user is
    /// still talking even while the recognizer's partial results stall
    /// (they routinely pause mid-word on long utterances — M87b).
    var lastVoiceActivity: ContinuousClock.Instant? { get }
}

enum SpeechEngineError: Error, LocalizedError {
    case onDeviceRecognitionUnavailable
    case modelNotReady

    var errorDescription: String? {
        switch self {
        case .onDeviceRecognitionUnavailable:
            return "On-device speech recognition isn't available for this language on this phone — and Family CFO never sends audio off the device."
        case .modelNotReady:
            return "The speech model is still downloading. Try again in a moment."
        }
    }
}

enum SpeechEngineFactory {
    /// The iOS 26 `SpeechAnalyzer` when the OS and language support it,
    /// otherwise the `SFSpeechRecognizer` fallback (pinned to on-device
    /// recognition).
    @MainActor
    static func make() -> SpeechEngine {
        if #available(iOS 26.0, *) {
            return AnalyzerSpeechEngine()
        }
        return RecognizerSpeechEngine()
    }
}

/// Shared microphone plumbing: a `.playAndRecord` session (so the reply can
/// be spoken through the same session) with echo cancellation where the
/// hardware provides it.
enum VoiceAudioSession {
    static func activate() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(
            .playAndRecord,
            mode: .spokenAudio,
            options: [.defaultToSpeaker, .allowBluetoothHFP]
        )
        try session.setActive(true, options: .notifyOthersOnDeactivation)
    }

    static func deactivate() {
        try? AVAudioSession.sharedInstance().setActive(
            false, options: .notifyOthersOnDeactivation)
    }
}
