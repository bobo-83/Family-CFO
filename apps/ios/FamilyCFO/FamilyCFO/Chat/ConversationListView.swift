import SwiftUI

/// Conversation history (M84): the entry point to the advisor. Server-side
/// memory and retrieval make old threads worth returning to.
struct ConversationListView: View {
    @Environment(AppModel.self) private var model
    @State private var conversations: [Components.Schemas.Conversation] = []
    @State private var isLoading = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if let errorMessage {
                    ContentUnavailableView {
                        Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("Retry") { Task { await load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else if conversations.isEmpty && !isLoading {
                    ContentUnavailableView {
                        Label("Ask your CFO anything", systemImage: "bubble.left.and.text.bubble.right")
                    } description: {
                        Text("Every answer is grounded in your household's own numbers.")
                    } actions: {
                        newChatButton.buttonStyle(.borderedProminent)
                    }
                } else {
                    List(conversations, id: \.id) { conversation in
                        NavigationLink(value: conversation.id) {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(conversation.title)
                                    .lineLimit(2)
                                Text(conversation.updatedAt, style: .relative)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .refreshable { await load() }
                }
            }
            .navigationTitle("Advisor")
            .toolbar {
                ToolbarItem(placement: .primaryAction) { newChatButton }
            }
            .navigationDestination(for: String.self) { conversationID in
                if let api = model.api {
                    ChatView(viewModel: ChatViewModel(api: api, conversationID: conversationID))
                }
            }
            .navigationDestination(for: NewChatRoute.self) { _ in
                if let api = model.api {
                    ChatView(viewModel: ChatViewModel(api: api))
                }
            }
            .task { await load() }
        }
    }

    private struct NewChatRoute: Hashable {}

    private var newChatButton: some View {
        NavigationLink(value: NewChatRoute()) {
            Label("New chat", systemImage: "square.and.pencil")
        }
    }

    private func load() async {
        guard let api = model.api else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            conversations = try await api.listConversations()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
