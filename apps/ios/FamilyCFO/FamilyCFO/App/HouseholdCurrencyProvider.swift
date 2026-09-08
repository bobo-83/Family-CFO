import Foundation
import Observation

/// The household's base currency, for every screen that must not guess it
/// (#156, ADR 0075). `GET /household` is the one endpoint that carries it, so
/// this is one memoised fetch per session: the Overview seeds it through
/// `LiveHouseholdAPI.onContext` (the callback that already seeds the
/// household language), and a screen opened first — Accounts, Goals — fetches
/// it once.
///
/// Rules, each pinned by a test:
/// - keyed by household AND session: signing out, re-pairing or signing in as
///   another household drops the value, and a completion that lands after the
///   session changed is discarded rather than cached under the new one;
/// - single-flight: concurrent callers share one in-flight Task;
/// - successes only are cached; a failure is reported and cleared, so the next
///   call retries instead of pinning the error for the rest of the session;
/// - never a default. `current` is nil until known, and callers keep their
///   forms closed until then — a literal "USD" is how a EUR household got USD
///   accounts (#152).
@MainActor
@Observable
final class HouseholdCurrencyProvider {
    enum Failure: Error {
        /// No paired session to ask for.
        case noSession
        /// The answer arrived for a session that is no longer current.
        case sessionChanged
    }

    /// Why the last fetch for the current session failed, or nil.
    private(set) var errorMessage: String?

    private var cached: (key: String, currency: String)?
    private var inflight: (key: String, task: Task<String, Error>)?

    private let sessionKey: @MainActor () -> String?
    private let fetch: @MainActor () async throws -> String

    /// - Parameters:
    ///   - sessionKey: identifies the household + session; nil when unpaired.
    ///   - fetch: the live context's currency for the current session.
    init(
        sessionKey: @escaping @MainActor () -> String?,
        fetch: @escaping @MainActor () async throws -> String
    ) {
        self.sessionKey = sessionKey
        self.fetch = fetch
    }

    /// The base currency for the CURRENT session, or nil while unknown.
    var current: String? {
        guard let cached, cached.key == sessionKey() else { return nil }
        return cached.currency
    }

    /// A screen that already holds the live context hands it over — no second
    /// fetch. `requestedIn` is the session key captured when that request was
    /// STARTED: a live-context response that lands after a sign-out and a
    /// pairing as another household would otherwise be stored under the new
    /// session (review of #158). Anything else is dropped.
    func seed(_ currency: String, requestedIn key: String) {
        guard key == sessionKey() else { return }
        cached = (key, currency)
        errorMessage = nil
    }

    /// Resolve the currency, fetching at most once per session at a time.
    func resolve() async throws -> String {
        guard let key = sessionKey() else { throw Failure.noSession }
        if let cached, cached.key == key { return cached.currency }
        if let inflight, inflight.key == key { return try await inflight.task.value }

        let fetch = self.fetch
        let task = Task<String, Error> { try await fetch() }
        inflight = (key, task)
        defer { if inflight?.key == key { inflight = nil } }
        do {
            let currency = try await task.value
            guard sessionKey() == key else { throw Failure.sessionChanged }
            cached = (key, currency)
            errorMessage = nil
            return currency
        } catch {
            if sessionKey() == key {
                errorMessage = ChatViewModel.describe(error)
            }
            throw error
        }
    }

    /// Forget everything: a different session may be a different household.
    func invalidate() {
        inflight?.task.cancel()
        inflight = nil
        cached = nil
        errorMessage = nil
    }
}
