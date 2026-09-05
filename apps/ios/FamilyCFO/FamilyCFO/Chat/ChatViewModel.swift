import Foundation
import Observation

@MainActor
@Observable
final class ChatViewModel {
    let api: AdvisorAPI
    private(set) var conversationID: String?
    private(set) var messages: [ChatMessage] = []
    private(set) var isSending = false
    /// Live one-line narration from the streamed turn (ADR 0061): what the
    /// advisor is doing right now ("Solving for your retirement age").
    private(set) var progressDetail: String?
    private(set) var isLoadingHistory = false
    var errorMessage: String?
    var pendingAttachment: ChatAttachment?
    /// A message staged by another screen — the M89 receipt capture opens chat
    /// with the receipt already asked about — sent once the view appears.
    var queuedMessage: String?
    private let recoveryPolicy: SavedAnswerRecovery.PollingPolicy

    init(
        api: AdvisorAPI,
        conversationID: String? = nil,
        recoveryPolicy: SavedAnswerRecovery.PollingPolicy = .standard
    ) {
        self.api = api
        self.conversationID = conversationID
        self.recoveryPolicy = recoveryPolicy
    }

    func sendQueuedMessageIfNeeded() async {
        guard let queued = queuedMessage else { return }
        queuedMessage = nil
        await send(queued)
    }

    /// Take over the conversation a voice session started, and pull its turns in.
    ///
    /// A hands-free session talks to the same `POST /chat/messages` pipeline, so
    /// the box creates a real conversation — but the ID came back to the VOICE
    /// view model, and used to die with it. The thread existed on the server and
    /// the app never showed it (user report, 2026-07-13). An UNCHANGED id still
    /// means new turns exist server-side — voice continued the open thread — so
    /// the transcript is always rebuilt from the server (user report, 2026-07-21).
    func adopt(conversationID id: String) async {
        conversationID = id
        messages = []
        await loadHistory()
    }

    func loadHistory() async {
        guard let conversationID, messages.isEmpty else { return }
        isLoadingHistory = true
        defer { isLoadingHistory = false }
        do {
            let detail = try await api.conversation(id: conversationID)
            messages = detail.messages
                .sorted { $0.sequence < $1.sequence }
                .map(ChatMessage.from)
        } catch {
            errorMessage = Self.describe(error)
        }
    }

    func send(_ text: String) async {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isSending else { return }

        let attachment = pendingAttachment
        pendingAttachment = nil
        var outgoing = ChatMessage(
            id: "local-\(messages.count)-\(trimmed.hashValue)",
            author: .user,
            text: trimmed
        )
        outgoing.attachmentName = attachment?.displayName
        messages.append(outgoing)
        isSending = true
        progressDetail = nil
        defer {
            isSending = false
            progressDetail = nil
        }

        do {
            let response = try await api.sendMessage(
                trimmed,
                conversationID: conversationID,
                attachment: attachment,
                onProgress: { [weak self] detail in
                    Task { @MainActor in self?.progressDetail = detail }
                }
            )
            conversationID = response.conversationId
            messages.append(.from(response.recommendation))
            errorMessage = nil
        } catch {
            // A long grounded answer can outlast the HTTP request — the socket
            // times out or drops, but the box finishes and SAVES the answer.
            // Before crying "disconnected", keep waiting and pull the saved
            // answer back (shared with voice mode; works even for the FIRST
            // message of a conversation, where the box mints the conversation
            // the phone never heard about). A truly offline phone errors at
            // once — SavedAnswerRecovery filters that out.
            if let recovered = await SavedAnswerRecovery(api: api).poll(
                after: error,
                utterance: trimmed,
                conversationID: conversationID,
                policy: recoveryPolicy)
            {
                conversationID = recovered.conversationID
                messages.append(ChatMessage.from(recovered.answer))
                errorMessage = nil
            } else {
                guard !Task.isCancelled else { return }
                errorMessage = Self.describe(error, during: .streamedTurn)
            }
        }
    }

    /// ADR 0044: rate an advisor answer. The rating shows immediately and
    /// reverts if the server rejects it; a failure never disrupts the chat.
    func rate(
        _ message: ChatMessage,
        _ rating: Components.Schemas.AdvisorFeedbackRequest.RatingPayload,
        note: String? = nil
    ) async {
        guard let recommendationId = message.recommendationId,
            let index = messages.firstIndex(where: { $0.id == message.id })
        else { return }
        let previous = messages[index].rating
        messages[index].rating = rating
        let trimmed = note?.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            try await api.submitFeedback(
                recommendationId: recommendationId,
                rating: rating,
                note: (trimmed?.isEmpty ?? true) ? nil : trimmed
            )
        } catch {
            messages[index].rating = previous
            errorMessage = Self.describe(error)
        }
    }

    static func describe(
        _ error: Error,
        during context: AdvisorErrorDescriber.RequestContext = .plainRequest
    ) -> String {
        AdvisorErrorDescriber.describe(error, during: context)
    }
}
