import AVFoundation
import Foundation

/// Tracks when the microphone last heard voice-loud audio (M87b fix).
///
/// The silence detector used to key ONLY on transcript changes — but the
/// recognizer's partial results routinely stall mid-word on long utterances
/// (revising a big hypothesis), so a 3 s stall while the user was mid-sentence
/// counted as "done" and cut the question off. Audio energy is the ground
/// truth for "still talking"; the transcript is only evidence of WHAT was said.
///
/// Called from the audio tap's realtime thread; reads come from the main
/// actor — hence the lock, and no allocation in `process`.
final class VoiceActivityMeter: @unchecked Sendable {
    /// Below this RMS nothing counts as voice no matter how quiet the room is.
    private static let minimumVoiceRMS: Float = 0.008
    /// Voice must rise this far above the ambient noise floor.
    private static let noiseFloorRatio: Float = 2.5

    private let lock = NSLock()
    private var lastVoice: ContinuousClock.Instant?
    private var noiseFloor: Float = 0.002

    func process(_ buffer: AVAudioPCMBuffer) {
        guard let channel = buffer.floatChannelData?[0] else { return }
        let frames = Int(buffer.frameLength)
        guard frames > 0 else { return }
        var sum: Float = 0
        for i in 0..<frames {
            let sample = channel[i]
            sum += sample * sample
        }
        let rms = (sum / Float(frames)).squareRoot()

        lock.lock()
        defer { lock.unlock() }
        if rms > max(Self.minimumVoiceRMS, noiseFloor * Self.noiseFloorRatio) {
            lastVoice = .now
        } else {
            // Only quiet buffers teach the floor, so speech can't raise it.
            noiseFloor = 0.95 * noiseFloor + 0.05 * rms
        }
    }

    var lastVoiceActivity: ContinuousClock.Instant? {
        lock.lock()
        defer { lock.unlock() }
        return lastVoice
    }
}
