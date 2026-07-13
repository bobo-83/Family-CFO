import SwiftUI

/// Conversation history (M84): the entry point to the advisor. Server-side
/// memory and retrieval make old threads worth returning to — and worth being
/// able to clear out, so the list stays usable.
struct ConversationListView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: ConversationListViewModel?
    @State private var path = NavigationPath()

    var body: some View {
        NavigationStack(path: $path) {
            Group {
                if let viewModel {
                    content(viewModel)
                } else {
                    ProgressView()
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
        }
        .task {
            if viewModel == nil, let api = model.api {
                viewModel = ConversationListViewModel(api: api)
            }
            await viewModel?.load()
        }
        // `task` runs once, so a conversation started while inside a chat — a new
        // thread, or one a hands-free voice session created — never appeared here
        // until the app was relaunched. Reload whenever we come back to the root.
        .onChange(of: path) { _, newPath in
            guard newPath.isEmpty else { return }
            Task { await viewModel?.load() }
        }
    }

    @ViewBuilder
    private func content(_ viewModel: ConversationListViewModel) -> some View {
        if let errorMessage = viewModel.errorMessage, viewModel.conversations.isEmpty {
            ContentUnavailableView {
                Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("Retry") { Task { await viewModel.load() } }
                    .buttonStyle(.borderedProminent)
            }
        } else if viewModel.conversations.isEmpty && !viewModel.isLoading {
            ContentUnavailableView {
                Label("Ask your CFO anything", systemImage: "bubble.left.and.text.bubble.right")
            } description: {
                Text("Every answer is grounded in your household's own numbers.")
            } actions: {
                newChatButton.buttonStyle(.borderedProminent)
            }
        } else {
            List {
                if let errorMessage = viewModel.errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                ForEach(viewModel.conversations, id: \.id) { conversation in
                    NavigationLink(value: conversation.id) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(conversation.title)
                                .lineLimit(2)
                            Text(conversation.updatedAt, style: .relative)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    // No confirmation: the swipe IS the deliberate act. The row
                    // only leaves the list once the box confirms the delete —
                    // and comes back if it refuses.
                    .swipeActions(edge: .trailing) {
                        Button(role: .destructive) {
                            Task { await viewModel.delete(id: conversation.id) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
            }
            .refreshable { await viewModel.load() }
        }
    }

    private struct NewChatRoute: Hashable {}

    private var newChatButton: some View {
        NavigationLink(value: NewChatRoute()) {
            Label("New chat", systemImage: "square.and.pencil")
        }
    }
}
