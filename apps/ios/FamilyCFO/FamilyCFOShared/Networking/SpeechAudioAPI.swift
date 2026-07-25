import Foundation
import OpenAPIRuntime

/// The on-box natural voice (`POST /voice/tts`, M87a) behind a seam, so the
/// synthesizer can be driven in tests without a server.
protocol SpeechAudioAPI: Sendable {
    /// MP3 bytes for one chunk of speech.
    func synthesize(_ text: String) async throws -> Data
}

enum SpeechAudioError: Error, LocalizedError, Equatable {
    /// The `tts` service is optional by design: the API answers 503 when it
    /// isn't deployed or is down, and every client falls back to platform
    /// speech (ADR 0018). This is a normal outcome, not a malfunction.
    case unavailable
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .unavailable:
            return "The natural voice service isn't running on the box."
        case .server(let status):
            return "The voice service answered with status \(status)."
        }
    }
}

enum AudioPlaybackError: Error, Equatable {
    case couldNotStart
}

struct LiveSpeechAudioAPI: SpeechAudioAPI {
    let client: Client
    /// nil lets the server choose its configured default voice.
    var voice: String? = nil

    func synthesize(_ text: String) async throws -> Data {
        let request = Components.Schemas.VoiceRequest(text: text, voice: voice)
        switch try await client.synthesizeSpeech(.init(body: .json(request))) {
        case .ok(let response):
            // One sentence of speech is tens of KB; the cap is a guard against
            // a misbehaving service, not a real limit.
            return try await Data(collecting: response.body.audioMpeg, upTo: 16 * 1024 * 1024)
        case .unauthorized:
            throw APIError.unauthorized
        case .serviceUnavailable:
            throw SpeechAudioError.unavailable
        case .undocumented(let status, _):
            throw SpeechAudioError.server(status)
        }
    }
}

