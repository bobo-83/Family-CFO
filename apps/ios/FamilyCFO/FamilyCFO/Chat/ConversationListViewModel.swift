import Foundation
import Observation

/// The conversation list's state. Extracted from the view so deleting a thread
/// — which is irreversible on the server — is testable.
@MainActor
@Observable
final class ConversationListViewModel {
    private(set) var conversations: [Components.Schemas.Conversation] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let api: AdvisorAPI

    init(api: AdvisorAPI) {
        self.api = api
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            conversations = try await api.listConversations()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Removes the row immediately, then deletes server-side. If the server
    /// refuses, the row comes BACK — a list that keeps a thread the box still
    /// has is a lie, and the user would think it was gone.
    func delete(id: String) async {
        guard let index = conversations.firstIndex(where: { $0.id == id }) else { return }
        let removed = conversations.remove(at: index)
        do {
            try await api.deleteConversation(id: id)
            errorMessage = nil
        } catch {
            conversations.insert(removed, at: min(index, conversations.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
