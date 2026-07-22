import AVFoundation
import Testing

@testable import FamilyCFO

struct VoiceActivityMeterTests {
    private func buffer(amplitude: Float, frames: AVAudioFrameCount = 1600) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(standardFormatWithSampleRate: 16_000, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buffer.frameLength = frames
        let channel = buffer.floatChannelData![0]
        for i in 0..<Int(frames) {
            // A 200 Hz tone at the requested amplitude.
            channel[i] = amplitude * sin(Float(i) * 2 * .pi * 200 / 16_000)
        }
        return buffer
    }

    @Test func speechLoudAudioRegistersActivity() {
        let meter = VoiceActivityMeter()
        #expect(meter.lastVoiceActivity == nil)

        meter.process(buffer(amplitude: 0.1))

        #expect(meter.lastVoiceActivity != nil)
    }

    @Test func roomToneNeverRegistersActivity() {
        let meter = VoiceActivityMeter()

        for _ in 0..<50 {
            meter.process(buffer(amplitude: 0.002))
        }

        #expect(meter.lastVoiceActivity == nil)
    }

    @Test func aNoisierRoomRaisesTheBarInsteadOfPinningActivity() {
        let meter = VoiceActivityMeter()
        // Steady moderate noise teaches the floor upward…
        for _ in 0..<200 {
            meter.process(buffer(amplitude: 0.007))
        }
        #expect(meter.lastVoiceActivity == nil)

        // …and real speech still clears it.
        meter.process(buffer(amplitude: 0.2))
        #expect(meter.lastVoiceActivity != nil)
    }
}
