import Foundation
import OpenAPIRuntime

/// Recovers an answer the box saved after the send's HTTP connection died.
///
/// A grounded answer can outlast the socket (idle for a minute on weak WiFi
/// while the model thinks — nginx logs 499), but the box finishes and saves
/// the turn. Both the text chat and voice mode share this: poll until the
/// sent utterance shows up as the last user message with an assistant reply
/// after it. When the send had no conversation yet (first message — the case
/// the earlier per-conversation recovery could not cover, user report
/// 2026-07-22), the newest conversations are checked instead: the box mints
/// the conversation even though the reply never reached the phone.
struct SavedAnswerRecovery {
    struct PollingPolicy: Sendable {
        static let standard = PollingPolicy(
            maximumAttempts: 20, interval: .seconds(6))

        let maximumAttempts: Int
        let interval: Duration

        init(maximumAttempts: Int, interval: Duration) {
            precondition(maximumAttempts > 0)
            self.maximumAttempts = maximumAttempts
            self.interval = interval
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

        for attempt in 0..<policy.maximumAttempts {
            if Task.isCancelled { return nil }
            if attempt > 0 {
                do {
                    try await Task.sleep(for: policy.interval)
                } catch {
                    return nil
                }
            }
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
                // Some transports finish successfully even after their caller
                // was cancelled. Never turn that late response into a recovered
                // answer after the owning chat/voice task has gone away.
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
        }
        return nil
    }

    /// Only a timed-out or truncated advisor stream may still be running on
    /// the box. Authentication, server, and genuinely-offline failures should
    /// surface immediately instead of spending two minutes polling in vain.
    static func isRecoverableStreamFailure(_ error: Error) -> Bool {
        let underlying = (error as? ClientError)?.underlyingError ?? error
        let nsError = underlying as NSError
        guard nsError.domain == NSURLErrorDomain else { return false }
        return nsError.code == NSURLErrorTimedOut
            || nsError.code == NSURLErrorNetworkConnectionLost
    }

    private func candidateIDs(_ known: String?) async -> [String] {
        if Task.isCancelled { return [] }
        if let known { return [known] }
        // Newest-first from the server; the just-minted conversation is at
        // the top, but allow a couple of slots for family members chatting
        // concurrently.
        guard let list = try? await api.listConversations() else { return [] }
        if Task.isCancelled { return [] }
        return list.prefix(3).map(\.id)
    }

    private func matches(stored: String, sent: String) -> Bool {
        // Attachment sends are stored with a suffix ("…\n\n[Photo: …]").
        stored == sent || stored.hasPrefix(sent + "\n\n[")
    }
}
