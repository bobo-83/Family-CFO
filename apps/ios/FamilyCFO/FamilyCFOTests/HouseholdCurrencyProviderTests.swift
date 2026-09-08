import Foundation
import Testing

@testable import FamilyCFO

/// #156 (ADR 0075): the household's base currency is fetched once per session,
/// shared between concurrent callers, never defaulted, and never allowed to
/// outlive the session it was fetched for.
@MainActor
struct HouseholdCurrencyProviderTests {
    /// A fetch the test releases by hand, to order completions against session
    /// changes. A one-shot latch: a result set before anyone waits is handed
    /// straight to the next waiter, so the test never depends on which side of
    /// a `Task.yield()` the fetch happens to be scheduled — a bare continuation
    /// list hung the whole run when `open` ran before `wait` had registered.
    @MainActor
    final class Gate {
        private var continuations: [CheckedContinuation<String, Error>] = []
        private var pending: Result<String, Error>?
        private(set) var calls = 0

        func wait() async throws -> String {
            calls += 1
            if let pending {
                self.pending = nil
                return try pending.get()
            }
            return try await withCheckedThrowingContinuation { continuations.append($0) }
        }

        /// Spin until the provider has actually called `fetch` (bounded).
        func untilCalled(times: Int = 1) async {
            for _ in 0..<1_000 where calls < times {
                await Task.yield()
            }
        }

        func open(_ currency: String) { settle(.success(currency)) }

        func fail(_ error: Error) { settle(.failure(error)) }

        private func settle(_ result: Result<String, Error>) {
            let waiting = continuations
            continuations = []
            if waiting.isEmpty {
                pending = result
            } else {
                waiting.forEach { $0.resume(with: result) }
            }
        }
    }

    struct Boom: Error {}

    @MainActor
    final class Session {
        var key: String? = "hh-1:device:token"
    }

    private func make(_ session: Session, _ gate: Gate) -> HouseholdCurrencyProvider {
        HouseholdCurrencyProvider(sessionKey: { session.key }, fetch: { try await gate.wait() })
    }

    @Test func unknownUntilResolvedThenCachedForTheSession() async throws {
        let session = Session()
        let gate = Gate()
        let provider = make(session, gate)
        #expect(provider.current == nil)

        let resolving = Task { try await provider.resolve() }
        await gate.untilCalled()
        gate.open("EUR")
        #expect(try await resolving.value == "EUR")
        #expect(provider.current == "EUR")
        // Cached: a second resolve asks nothing.
        #expect(try await provider.resolve() == "EUR")
        #expect(gate.calls == 1)
    }

    @Test func concurrentCallersShareOneFetch() async throws {
        let session = Session()
        let gate = Gate()
        let provider = make(session, gate)

        let first = Task { try await provider.resolve() }
        let second = Task { try await provider.resolve() }
        await gate.untilCalled()
        // Let the second caller reach the in-flight check before releasing.
        for _ in 0..<20 { await Task.yield() }
        gate.open("EUR")

        #expect(try await first.value == "EUR")
        #expect(try await second.value == "EUR")
        #expect(gate.calls == 1)
    }

    @Test func failureIsReportedNotCachedAndRetried() async throws {
        let session = Session()
        let gate = Gate()
        let provider = make(session, gate)

        let failing = Task { try await provider.resolve() }
        await gate.untilCalled()
        gate.fail(Boom())
        await #expect(throws: Boom.self) { try await failing.value }
        #expect(provider.current == nil)
        #expect(provider.errorMessage != nil)

        let retry = Task { try await provider.resolve() }
        await gate.untilCalled(times: 2)
        gate.open("EUR")
        #expect(try await retry.value == "EUR")
        #expect(provider.errorMessage == nil)
        #expect(gate.calls == 2)
    }

    @Test func aNewSessionDropsTheValueAndFetchesItsOwn() async throws {
        let session = Session()
        let gate = Gate()
        let provider = make(session, gate)
        provider.seed("EUR", requestedIn: session.key!)
        #expect(provider.current == "EUR")

        // Sign out: no session, nothing to show, nothing to ask for.
        session.key = nil
        #expect(provider.current == nil)
        await #expect(throws: HouseholdCurrencyProvider.Failure.self) { try await provider.resolve() }

        // Another household signs in on the same device.
        session.key = "hh-2:device:other-token"
        #expect(provider.current == nil)
        let resolving = Task { try await provider.resolve() }
        await gate.untilCalled()
        gate.open("VND")
        #expect(try await resolving.value == "VND")
        #expect(provider.current == "VND")
    }

    @Test func aLateCompletionForTheOldSessionIsDiscarded() async throws {
        let session = Session()
        let gate = Gate()
        let provider = make(session, gate)

        let stale = Task { try await provider.resolve() }
        await gate.untilCalled()
        // The user signs out and someone else signs in before hh-1's answer lands.
        session.key = "hh-2:device:other-token"
        gate.open("EUR")

        await #expect(throws: HouseholdCurrencyProvider.Failure.self) { try await stale.value }
        #expect(provider.current == nil)
        #expect(provider.errorMessage == nil)  // not this session's failure either
    }

    @Test func invalidateForgetsTheValue() {
        let session = Session()
        let provider = make(session, Gate())
        provider.seed("EUR", requestedIn: session.key!)
        provider.invalidate()
        #expect(provider.current == nil)
    }

    /// Review of #158: the live-context callback that seeds the currency was
    /// built for session A; its response lands after A signed out and B paired.
    @Test func aSeedRequestedInAnOldSessionIsDropped() {
        let session = Session()
        let provider = make(session, Gate())
        let requestedIn = session.key!

        session.key = "hh-2:device:other-token"
        provider.seed("EUR", requestedIn: requestedIn)

        #expect(provider.current == nil)
        // B's own seed, keyed to B's session, still lands.
        provider.seed("VND", requestedIn: session.key!)
        #expect(provider.current == "VND")
    }
}
