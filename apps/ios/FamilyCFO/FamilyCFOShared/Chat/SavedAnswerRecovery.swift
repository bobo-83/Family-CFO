import Foundation
import OpenAPIRuntime

/// Recovers an answer the box saved after the send's HTTP connection died.
///
/// M95/#125: the server advertises the remaining lifetime of its bounded turn.
/// Check immediately, then back off until that monotonic deadline instead of
/// giving up after an unrelated fixed attempt count. Older servers use the
/// same ten-minute compatibility horizon as the server default.
struct SavedAnswerRecovery {
    struct PollingPolicy: Sendable {
        static let standard = PollingPolicy(
            fallbackHorizon: .seconds(600),
            initialInterval: .seconds(2),
            maximumInterval: .seconds(15)
        )

        let fallbackHorizon: Duration
        let initialInterval: Duration
        let maximumInterval: Duration
        /// Test-only compatibility seam: production is deadline-based.
        let maximumAttempts: Int?

        init(
            fallbackHorizon: Duration,
            initialInterval: Duration,
            maximumInterval: Duration,
            maximumAttempts: Int? = nil
        ) {
            precondition(fallbackHorizon > .zero)
            precondition(initialInterval >= .zero)
            precondition(maximumInterval >= initialInterval)
            if let maximumAttempts { precondition(maximumAttempts > 0) }
            self.fallbackHorizon = fallbackHorizon
            self.initialInterval = initialInterval
            self.maximumInterval = maximumInterval
            self.maximumAttempts = maximumAttempts
        }

        /// Keeps existing fast unit tests source-compatible; production never
        /// uses an attempt limit.
        init(maximumAttempts: Int, interval: Duration) {
            self.init(
                fallbackHorizon: .seconds(600),
                initialInterval: interval,
                maximumInterval: interval,
                maximumAttempts: maximumAttempts
            )
        }
    }

    let api: AdvisorAPI

    func poll(
        after error: Error,
        utterance: String,
        conversationID: String?,
        policy: PollingPolicy = .standard
    ) async -> (conversationID: String, answer: Components.Schemas.ConversationMessage)? {
        guard Self.isRecoverableStreamFailure(error) else { return nil }

        let clock = ContinuousClock()
        let serverDeadline = Self.streamFailure(error)?.recoveryDeadline
        let deadline = serverDeadline ?? clock.now.advanced(by: policy.fallbackHorizon)
        var interval = policy.initialInterval
        var attempts = 0

        while true {
            if Task.isCancelled { return nil }
            attempts += 1
            if let recovered = await lookup(
                utterance: utterance, conversationID: conversationID)
            {
                return Task.isCancelled ? nil : recovered
            }
            if let maximumAttempts = policy.maximumAttempts,
                attempts >= maximumAttempts
            {
                return nil
            }

            let now = clock.now
            if now >= deadline { return nil }
            let remaining = now.duration(to: deadline)
            let sleepFor = min(interval, remaining)
            do {
                try await Task.sleep(for: sleepFor)
            } catch {
                return nil
            }
            // A sleep capped to the deadline is followed by one final lookup.
            interval = min(interval * 2, policy.maximumInterval)
        }
    }

    private func lookup(
        utterance: String, conversationID: String?
    ) async -> (conversationID: String, answer: Components.Schemas.ConversationMessage)? {
        let candidateIDs = await candidateIDs(conversationID)
        if Task.isCancelled { return nil }
        for id in candidateIDs {
            if Task.isCancelled { return nil }
            let detail: Components.Schemas.ConversationDetail
            do {
                detail = try await api.conversation(id: id)
            } catch {
                if Task.isCancelled { return nil }
                continue
            }
            if Task.isCancelled { return nil }
            let ordered = detail.messages.sorted { $0.sequence < $1.sequence }
            guard
                let userIndex = ordered.lastIndex(where: { $0.role == .user }),
                matches(stored: ordered[userIndex].content, sent: utterance),
                userIndex + 1 < ordered.count,
                ordered[userIndex + 1].role == .assistant
            else { continue }
            return (id, ordered[userIndex + 1])
        }
        return nil
    }

    /// Only a timed-out or truncated advisor stream may still be running on
    /// the box. Authentication, server, and genuinely-offline failures surface
    /// immediately instead of polling in vain.
    static func isRecoverableStreamFailure(_ error: Error) -> Bool {
        let underlying = transportError(error)
        let nsError = underlying as NSError
        guard nsError.domain == NSURLErrorDomain else { return false }
        return nsError.code == NSURLErrorTimedOut
            || nsError.code == NSURLErrorNetworkConnectionLost
    }

    private static func streamFailure(_ error: Error) -> AdvisorStreamFailure? {
        if let failure = error as? AdvisorStreamFailure { return failure }
        if let clientError = error as? ClientError {
            return streamFailure(clientError.underlyingError)
        }
        return nil
    }

    static func transportError(_ error: Error) -> Error {
        if let failure = error as? AdvisorStreamFailure {
            return transportError(failure.underlyingError)
        }
        if let clientError = error as? ClientError {
            return transportError(clientError.underlyingError)
        }
        return error
    }

    private func candidateIDs(_ known: String?) async -> [String] {
        if Task.isCancelled { return [] }
        if let known { return [known] }
        guard let list = try? await api.listConversations() else { return [] }
        if Task.isCancelled { return [] }
        return list.prefix(3).map(\.id)
    }

    private func matches(stored: String, sent: String) -> Bool {
        stored == sent || stored.hasPrefix(sent + "\n\n[")
    }
}
