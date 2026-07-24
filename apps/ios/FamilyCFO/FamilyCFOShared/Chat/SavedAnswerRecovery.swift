import Foundation

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
    let api: AdvisorAPI

    func poll(
        utterance: String, conversationID: String?
    ) async -> (conversationID: String, answer: Components.Schemas.ConversationMessage)? {
        for attempt in 0..<20 {  // ~2 min beyond the request that already died
            if Task.isCancelled { return nil }
            if attempt > 0 { try? await Task.sleep(for: .seconds(6)) }
            for id in await candidateIDs(conversationID) {
                guard let detail = try? await api.conversation(id: id) else { continue }
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

    private func candidateIDs(_ known: String?) async -> [String] {
        if let known { return [known] }
        // Newest-first from the server; the just-minted conversation is at
        // the top, but allow a couple of slots for family members chatting
        // concurrently.
        guard let list = try? await api.listConversations() else { return [] }
        return list.prefix(3).map(\.id)
    }

    private func matches(stored: String, sent: String) -> Bool {
        // Attachment sends are stored with a suffix ("…\n\n[Photo: …]").
        stored == sent || stored.hasPrefix(sent + "\n\n[")
    }
}
