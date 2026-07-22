import AVFoundation
import Foundation
import Speech

/// Pre-iOS-26 fallback: `SFSpeechRecognizer` pinned to on-device
/// recognition. If the device/language cannot recognize on-device we FAIL
/// rather than fall back to Apple's servers — the ADR 0018 privacy
/// guarantee (raw audio never leaves the phone) outranks convenience.
@MainActor
final class RecognizerSpeechEngine: SpeechEngine {
    private let audioEngine = AVAudioEngine()
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let activityMeter = VoiceActivityMeter()

    var lastVoiceActivity: ContinuousClock.Instant? { activityMeter.lastVoiceActivity }

    func requestPermission() async -> Bool {
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
        guard speech else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }

    func startTranscribing() async throws -> AsyncStream<String> {
        stopTranscribing()

        guard
            let recognizer = SFSpeechRecognizer(locale: .current)
                ?? SFSpeechRecognizer(locale: Locale(identifier: "en_US")),
            recognizer.isAvailable,
            recognizer.supportsOnDeviceRecognition
        else {
            throw SpeechEngineError.onDeviceRecognitionUnavailable
        }

        try VoiceAudioSession.activate()

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = true
        recognitionRequest = request

        let inputFormat = audioEngine.inputNode.outputFormat(forBus: 0)
        // A leftover tap (from an interrupted/failed earlier session) would
        // crash Core Audio on install; removing when none exists is a no-op.
        audioEngine.inputNode.removeTap(onBus: 0)
        let meter = activityMeter
        audioEngine.inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) {
            buffer, _ in
            meter.process(buffer)
            request.append(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()

        let (stream, continuation) = AsyncStream<String>.makeStream()
        recognitionTask = recognizer.recognitionTask(with: request) { result, error in
            if let result {
                continuation.yield(result.bestTranscription.formattedString)
                if result.isFinal {
                    continuation.finish()
                }
            }
            if error != nil {
                continuation.finish()
            }
        }
        return stream
    }

    func stopTranscribing() {
        // Unconditional: a partially-started session must still tear down.
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        recognitionTask?.cancel()
        recognitionTask = nil
        VoiceAudioSession.deactivate()
    }
}
