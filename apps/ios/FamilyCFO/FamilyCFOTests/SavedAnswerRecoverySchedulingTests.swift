import Foundation
import Testing

@testable import FamilyCFO

/// Virtual time for SavedAnswerRecovery (M95): "sleeping" advances `now`
/// instantly and records the requested duration, so backoff shape, the
/// deadline cap, and the final lookup are proven without real waits.
final class VirtualRecoveryClock: @unchecked Sendable {
    private let lock = NSLock()
    private var current = ContinuousClock.now
    private var recorded: [Duration] = []

    var now: ContinuousClock.Instant { lock.withLock { current } }
    var sleeps: [Duration] { lock.withLock { recorded } }
    var elapsed: Duration { lock.withLock { recorded.reduce(.zero, +) } }

    var scheduler: SavedAnswerRecovery.Scheduler {
        SavedAnswerRecovery.Scheduler(
            now: { [self] in lock.withLock { current } },
            sleep: { [self] duration in
                // Mirror Task.sleep: a cancelled task does not keep sleeping.
                if Task.isCancelled { throw CancellationError() }
                lock.withLock {
                    recorded.append(duration)
                    current = current.advanced(by: duration)
                }
            }
        )
    }
}

@MainActor
struct SavedAnswerRecoverySchedulingTests {
    private func unansweredDetail(id: String = "conv-1") -> Components.Schemas.ConversationDetail {
        .init(id: id, title: "Still working", createdAt: .now, updatedAt: .now, messages: [])
    }

    private func answeredDetail(utterance: String) -> Components.Schemas.ConversationDetail {
        .init(
            id: "conv-1", title: "Plan", createdAt: .now, updatedAt: .now,
            messages: [
                .init(id: "u1", role: .user, content: utterance, sequence: 1, createdAt: .now),
                .init(
                    id: "a1", role: .assistant, content: "Here's your plan…",
                    recommendationId: "rec-9", sequence: 2, createdAt: .now),
            ]
        )
    }

    @Test func backoffDoublesToTheCapAndTheLastSleepIsCappedToTheDeadline() async {
        let api = MockAdvisorAPI()
        api.detail = unansweredDetail()
        let clock = VirtualRecoveryClock()
        let policy = SavedAnswerRecovery.PollingPolicy(
            fallbackHorizon: .seconds(60),
            initialInterval: .seconds(2),
            maximumInterval: .seconds(15))

        let recovered = await SavedAnswerRecovery(api: api).poll(
            after: NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut),
            utterance: "take your time",
            conversationID: "conv-1",
            policy: policy,
            scheduler: clock.scheduler)

        #expect(recovered == nil)
        // 2 → 4 → 8, capped at 15, and a final sleep capped to the 1s left.
        #expect(
            clock.sleeps == [
                .seconds(2), .seconds(4), .seconds(8),
                .seconds(15), .seconds(15), .seconds(15), .seconds(1),
            ])
        // An immediate first lookup, then one more after every sleep —
        // including one final lookup after the deadline-capped sleep.
        #expect(api.conversationRequests.count == clock.sleeps.count + 1)
    }

    @Test func aServerDeadlineOutlivesBothTheFallbackAndTheOldTwoMinuteWindow() async {
        let api = MockAdvisorAPI()
        api.detail = unansweredDetail()
        let clock = VirtualRecoveryClock()
        let failure = AdvisorStreamFailure(
            underlyingError: NSError(
                domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost),
            recoveryDeadline: clock.now.advanced(by: .seconds(180)))
        let policy = SavedAnswerRecovery.PollingPolicy(
            fallbackHorizon: .seconds(60),
            initialInterval: .seconds(2),
            maximumInterval: .seconds(15))

        let recovered = await SavedAnswerRecovery(api: api).poll(
            after: failure,
            utterance: "take your time",
            conversationID: "conv-1",
            policy: policy,
            scheduler: clock.scheduler)

        #expect(recovered == nil)
        // The advertised deadline governs: past the 60s fallback AND past the
        // pre-M95 fixed two-minute window, to the exact server horizon.
        #expect(clock.elapsed == .seconds(180))
    }

    @Test func theFinalDeadlineCappedLookupCanStillRecoverABuzzerBeaterAnswer() async {
        let api = MockAdvisorAPI()
        api.detail = unansweredDetail()
        let clock = VirtualRecoveryClock()
        let failure = AdvisorStreamFailure(
            underlyingError: NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut),
            recoveryDeadline: clock.now.advanced(by: .seconds(30)))
        let base = clock.scheduler
        let answered = answeredDetail(utterance: "take your time")
        // The box "saves" the answer during the final capped 1-second sleep
        // (2 + 4 + 8 + 15 = 29s elapsed, 1s left on the 30s horizon).
        let scheduler = SavedAnswerRecovery.Scheduler(
            now: base.now,
            sleep: { duration in
                try await base.sleep(duration)
                if clock.elapsed >= .seconds(30) {
                    api.detail = answered
                }
            })

        let recovered = await SavedAnswerRecovery(api: api).poll(
            after: failure,
            utterance: "take your time",
            conversationID: "conv-1",
            policy: .standard,
            scheduler: scheduler)

        #expect(clock.sleeps == [.seconds(2), .seconds(4), .seconds(8), .seconds(15), .seconds(1)])
        #expect(recovered?.conversationID == "conv-1")
        #expect(recovered?.answer.content == "Here's your plan…")
    }

    @Test func cancellationEndsThePollQuietlyWithNoAnswer() async {
        let api = MockAdvisorAPI()
        api.detail = unansweredDetail()
        let clock = VirtualRecoveryClock()

        let task = Task {
            await SavedAnswerRecovery(api: api).poll(
                after: NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut),
                utterance: "take your time",
                conversationID: "conv-1",
                policy: .standard,
                scheduler: clock.scheduler)
        }
        task.cancel()

        let recovered = await task.value
        #expect(recovered == nil)
    }
}
