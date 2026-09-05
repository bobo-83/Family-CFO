import Foundation
import Testing

@testable import FamilyCFO

@MainActor
struct AdvisorErrorDescriberTests {
    @Test func aLostConnectionUsesTheRequestContext() {
        let error = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost)

        let plain = AdvisorErrorDescriber.describe(error)
        let streamed = AdvisorErrorDescriber.describe(error, during: .streamedTurn)

        #expect(plain.contains("Local Network"))
        #expect(!plain.contains("advisor was still working"))
        #expect(streamed.contains("advisor was still working"))
        #expect(!streamed.contains("Local Network"))
    }

    @Test func aTimedOutStreamUsesTheRequestContext() {
        let error = NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut)

        let plain = AdvisorErrorDescriber.describe(error)
        let streamed = AdvisorErrorDescriber.describe(error, during: .streamedTurn)

        #expect(plain.contains("Try again in a minute"))
        #expect(!plain.contains("advisor was still working"))
        #expect(streamed.contains("advisor was still working"))
        #expect(!streamed.contains("Try again"))
    }

    /// M95 truthful exhaustion: this copy is shown only after
    /// SavedAnswerRecovery polled past the server's own advertised bound, so
    /// the turn provably saved nothing — say so, and make resending safe.
    @Test func exhaustionAgainstAnAdvertisedHorizonSaysTheTurnDidNotComplete() {
        let failure = AdvisorStreamFailure(
            underlyingError: NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut),
            recoveryDeadline: ContinuousClock.now)

        let text = AdvisorErrorDescriber.describe(failure, during: .streamedTurn)

        #expect(text.contains("advisor was still working"))
        #expect(text.contains("didn't complete"))
        #expect(text.contains("safe to send your message again"))
        #expect(!text.contains("may still finish"))
    }

    /// Without an advertised horizon (older server, or the drop preceded the
    /// response headers) the box may genuinely still be working — keep issue
    /// #124's caution against resending a turn that may yet be saved.
    @Test func exhaustionWithoutAnAdvertisedHorizonStaysCautious() {
        let failure = AdvisorStreamFailure(
            underlyingError: NSError(
                domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost),
            recoveryDeadline: nil)

        let text = AdvisorErrorDescriber.describe(failure, during: .streamedTurn)

        #expect(text.contains("advisor was still working"))
        #expect(text.contains("may still finish"))
        #expect(text.contains("before resending"))
        #expect(!text.contains("didn't complete"))
    }

    @Test func onlyTimeoutsAndLostConnectionsAttemptSavedAnswerRecovery() async {
        let timeout = NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut)
        let lost = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorNetworkConnectionLost)
        let offline = NSError(
            domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)

        #expect(SavedAnswerRecovery.isRecoverableStreamFailure(timeout))
        #expect(SavedAnswerRecovery.isRecoverableStreamFailure(lost))
        #expect(!SavedAnswerRecovery.isRecoverableStreamFailure(offline))
        #expect(!SavedAnswerRecovery.isRecoverableStreamFailure(APIError.server(500)))

        let api = MockAdvisorAPI()
        let recovered = await SavedAnswerRecovery(api: api).poll(
            after: offline,
            utterance: "hello",
            conversationID: "conv-1",
            policy: .init(maximumAttempts: 1, interval: .seconds(0)))

        #expect(recovered == nil)
        #expect(api.conversationRequests.isEmpty)
        #expect(api.listConversationCallCount == 0)
    }
}
