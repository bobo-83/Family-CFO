import Foundation
import OpenAPIRuntime
import Testing

@testable import FamilyCFO

/// Pins the SSE frame parsing for the streamed chat turn (ADR 0061).
@MainActor
struct AdvisorStreamParsingTests {
    private final class ProgressLog: @unchecked Sendable {
        private let lock = NSLock()
        private var _lines: [String] = []
        var lines: [String] {
            lock.lock()
            defer { lock.unlock() }
            return _lines
        }
        func append(_ line: String) {
            lock.lock()
            defer { lock.unlock() }
            _lines.append(line)
        }
    }

    @Test func parsesProgressThenAnswer() async throws {
        let stream = """
            data: {"type": "progress", "stage": "thinking", "detail": "Thinking with your numbers"}

            : ping

            data: {"type": "progress", "stage": "tool", "tool": "when_can_i_retire", "detail": "Solving for your retirement age"}

            data: {"type": "answer", "response": {"conversation_id": "conv-9", "recommendation": {"id": "rec-1", "answer": "At 67.", "assumptions": [], "impacts": [], "tradeoffs": [], "alternatives": [], "confidence": 0.8, "calculation_refs": [], "warnings": []}}}

            """
        let log = ProgressLog()

        let response = try await LiveAdvisorAPI.consumeEventStream(
            HTTPBody(stream), onProgress: { log.append($0) })

        #expect(log.lines == ["Thinking with your numbers", "Solving for your retirement age"])
        #expect(response.conversationId == "conv-9")
        #expect(response.recommendation.answer == "At 67.")
    }

    @Test func anErrorEventThrowsTheAdvisorsOwnMessage() async {
        let stream = """
            data: {"type": "error", "message": "The advisor hit an unexpected error."}

            """
        await #expect(throws: APIError.advisor("The advisor hit an unexpected error.")) {
            _ = try await LiveAdvisorAPI.consumeEventStream(
                HTTPBody(stream), onProgress: { _ in })
        }
    }

    @Test func aTruncatedStreamReadsAsAConnectionLoss() async {
        // No answer event before EOF — must map to the error class that
        // SavedAnswerRecovery treats as "the box may still have saved it".
        let stream = """
            data: {"type": "progress", "stage": "thinking", "detail": "Thinking"}

            """
        do {
            _ = try await LiveAdvisorAPI.consumeEventStream(
                HTTPBody(stream), onProgress: { _ in })
            Issue.record("expected a throw")
        } catch {
            #expect(ChatViewModel.mightStillBeGenerating(error))
        }
    }

    @Test func chunkBoundariesInsideAFrameDoNotBreakParsing() async throws {
        // The network hands us arbitrary chunk sizes; a frame split mid-JSON
        // must reassemble.
        let frame = "data: {\"type\": \"answer\", \"response\": {\"conversation_id\": \"c\", \"recommendation\": {\"id\": \"r\", \"answer\": \"ok\", \"assumptions\": [], \"impacts\": [], \"tradeoffs\": [], \"alternatives\": [], \"confidence\": 0.5, \"calculation_refs\": [], \"warnings\": []}}}\n\n"
        let bytes = Array(frame.utf8)
        let mid = bytes.count / 3
        let chunks: [ArraySlice<UInt8>] = [
            bytes[..<mid], bytes[mid..<(2 * mid)], bytes[(2 * mid)...],
        ]
        let body = HTTPBody(
            AsyncStream { continuation in
                for chunk in chunks { continuation.yield(chunk) }
                continuation.finish()
            },
            length: .unknown
        )

        let response = try await LiveAdvisorAPI.consumeEventStream(body, onProgress: { _ in })

        #expect(response.recommendation.answer == "ok")
    }
}
