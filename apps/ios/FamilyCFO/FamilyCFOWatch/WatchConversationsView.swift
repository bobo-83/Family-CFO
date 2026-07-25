import SwiftUI

/// Past advisor threads on the wrist (ADR 0067 v4): reopen one, or swipe it
/// away. Same ConversationListViewModel as the phone's Advisor tab, so the
/// rules match — a row only leaves the list once the box confirms the delete,
/// and comes back if it refuses.
struct WatchConversationsView: View {
    @Environment(WatchModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var viewModel: ConversationListViewModel?
    /// Hands the chosen thread back to the chat page, which loads it.
    let onSelect: (String) -> Void

    var body: some View {
        Group {
            if let viewModel {
                content(viewModel)
            } else {
                ProgressView()
            }
        }
        // Clear of the page-indicator dots (user report 2026-07-25).
        .contentMargins(.trailing, 10, for: .scrollContent)
        // The chat page's Talk/Type bottom bar bled onto this pushed screen
        // (sim rig, 2026-07-25) — hide it; rows are the only actions here.
        .toolbar(.hidden, for: .bottomBar)
        .navigationTitle("Chats")
        .task {
            if viewModel == nil, let api = model.advisor {
                viewModel = ConversationListViewModel(api: api)
            }
            await viewModel?.load()
        }
    }

    @ViewBuilder private func content(_ viewModel: ConversationListViewModel) -> some View {
        if viewModel.conversations.isEmpty && !viewModel.isLoading {
            VStack(spacing: 6) {
                Image(systemName: "bubble.left.and.text.bubble.right")
                Text(viewModel.errorMessage ?? "No conversations yet.")
                    .font(.footnote)
                    .multilineTextAlignment(.center)
                    .foregroundStyle(viewModel.errorMessage == nil ? .secondary : Color.red)
            }
            .padding()
        } else {
            List {
                if let errorMessage = viewModel.errorMessage {
                    Text(errorMessage).font(.caption2).foregroundStyle(.red)
                }
                ForEach(viewModel.conversations, id: \.id) { conversation in
                    Button {
                        onSelect(conversation.id)
                        dismiss()
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(conversation.title)
                                .font(.footnote)
                                .lineLimit(2)
                            Text(conversation.updatedAt, style: .relative)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                    // The swipe IS the deliberate act, as on the phone.
                    .swipeActions(edge: .trailing) {
                        Button(role: .destructive) {
                            Task { await viewModel.delete(id: conversation.id) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
            }
        }
    }
}
