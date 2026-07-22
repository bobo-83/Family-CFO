import AVFoundation
import Foundation
import Speech

/// iOS 26 `SpeechAnalyzer`/`SpeechTranscriber` engine (ADR 0018's preferred
/// path): Whisper-class accuracy, entirely on device. The language model is
/// downloaded once by the system on first use.
@available(iOS 26.0, *)
@MainActor
final class AnalyzerSpeechEngine: SpeechEngine {
    private let audioEngine = AVAudioEngine()
    private var analyzer: SpeechAnalyzer?
    private var inputBuilder: AsyncStream<AnalyzerInput>.Continuation?
    private var recognitionTask: Task<Void, Never>?
    private var outputContinuation: AsyncStream<String>.Continuation?
    private let activityMeter = VoiceActivityMeter()

    var lastVoiceActivity: ContinuousClock.Instant? { activityMeter.lastVoiceActivity }

    func requestPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func startTranscribing() async throws -> AsyncStream<String> {
        stopTranscribing()

        let locale = await SpeechTranscriber.supportedLocale(equivalentTo: .current) ?? Locale(identifier: "en_US")
        let transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: []
        )

        // Make sure the on-device model for this locale is installed; the
        // download happens once, system-managed, and can be awaited.
        let status = await AssetInventory.status(forModules: [transcriber])
        if status == .unsupported {
            throw SpeechEngineError.onDeviceRecognitionUnavailable
        }
        if status != .installed {
            if let request = try await AssetInventory.assetInstallationRequest(
                supporting: [transcriber])
            {
                try await request.downloadAndInstall()
            }
        }

        guard
            let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber])
        else {
            throw SpeechEngineError.onDeviceRecognitionUnavailable
        }

        try VoiceAudioSession.activate()

        let (inputSequence, inputBuilder) = AsyncStream<AnalyzerInput>.makeStream()
        self.inputBuilder = inputBuilder
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        self.analyzer = analyzer
        try await analyzer.start(inputSequence: inputSequence)

        let inputFormat = audioEngine.inputNode.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: inputFormat, to: analyzerFormat) else {
            throw SpeechEngineError.onDeviceRecognitionUnavailable
        }
        // A leftover tap (from an interrupted/failed earlier session) would
        // crash Core Audio on install; removing when none exists is a no-op.
        audioEngine.inputNode.removeTap(onBus: 0)
        let meter = activityMeter
        audioEngine.inputNode.installTap(onBus: 0, bufferSize: 4096, format: inputFormat) {
            buffer, _ in
            meter.process(buffer)
            let ratio = analyzerFormat.sampleRate / inputFormat.sampleRate
            let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
            guard
                let converted = AVAudioPCMBuffer(
                    pcmFormat: analyzerFormat, frameCapacity: capacity)
            else { return }
            var consumed = false
            var conversionError: NSError?
            converter.convert(to: converted, error: &conversionError) { _, inputStatus in
                if consumed {
                    inputStatus.pointee = .noDataNow
                    return nil
                }
                consumed = true
                inputStatus.pointee = .haveData
                return buffer
            }
            if conversionError == nil, converted.frameLength > 0 {
                inputBuilder.yield(AnalyzerInput(buffer: converted))
            }
        }
        audioEngine.prepare()
        try audioEngine.start()

        let (outputStream, outputContinuation) = AsyncStream<String>.makeStream()
        self.outputContinuation = outputContinuation

        // Finalized segments accumulate; volatile text is the live tail that
        // each new volatile result replaces.
        recognitionTask = Task { [weak self] in
            var finalized = ""
            var volatile = ""
            do {
                for try await result in transcriber.results {
                    let text = String(result.text.characters)
                    if result.isFinal {
                        finalized += text
                        volatile = ""
                    } else {
                        volatile = text
                    }
                    self?.outputContinuation?.yield(finalized + volatile)
                }
            } catch {
                // Recognition ending (stop or failure) just finishes the
                // stream; the session view model decides what to show.
            }
            self?.outputContinuation?.finish()
        }

        return outputStream
    }

    func stopTranscribing() {
        // Unconditional: a partially-started session (e.g. audio engine
        // failed after the tap was installed) must still be torn down.
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        inputBuilder?.finish()
        inputBuilder = nil
        if let analyzer {
            self.analyzer = nil
            Task {
                try? await analyzer.finalizeAndFinishThroughEndOfInput()
            }
        }
        recognitionTask = nil
        VoiceAudioSession.deactivate()
    }
}
