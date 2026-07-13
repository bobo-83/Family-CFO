import SwiftUI

/// Quick transaction categorization (M91): the uncategorized transactions in a
/// list, each swiped to assign a category, with an undo for the last one.
/// Adults-only — categorizing changes household money data.
struct CategorizeView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: CategorizeViewModel?
    @State private var picking: Components.Schemas.Transaction?

    var body: some View {
        NavigationStack {
            Group {
                if let viewModel {
                    content(viewModel)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Categorize")
        }
        .task {
            if viewModel == nil, let api = model.categorize {
                viewModel = CategorizeViewModel(api: api)
            }
            await viewModel?.load()
        }
    }

    @ViewBuilder
    private func content(_ viewModel: CategorizeViewModel) -> some View {
        if let errorMessage = viewModel.errorMessage, viewModel.transactions.isEmpty {
            ContentUnavailableView {
                Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("Retry") { Task { await viewModel.load() } }
                    .buttonStyle(.borderedProminent)
            }
        } else if viewModel.transactions.isEmpty && !viewModel.isLoading {
            ContentUnavailableView {
                Label("All caught up", systemImage: "checkmark.circle")
            } description: {
                Text("Every transaction has a category.")
            }
        } else {
            VStack(spacing: 0) {
                List {
                    if let errorMessage = viewModel.errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                    // Categorizing needs categories to assign, and category
                    // management lives on the web dashboard (a mobile-spec
                    // non-responsibility) — so point there rather than offer a
                    // swipe that opens an empty picker.
                    if viewModel.categories.isEmpty {
                        Label(
                            "Create categories on the dashboard's Categories page to sort these.",
                            systemImage: "info.circle"
                        )
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    }
                    ForEach(viewModel.transactions, id: \.id) { transaction in
                        if viewModel.categories.isEmpty {
                            row(transaction)
                        } else {
                            row(transaction)
                                .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                    Button {
                                        picking = transaction
                                    } label: {
                                        Label("Categorize", systemImage: "tag")
                                    }
                                    .tint(.accentColor)
                                }
                        }
                    }
                }
                .refreshable { await viewModel.load() }

                if let action = viewModel.lastAction {
                    undoBar(action, viewModel)
                }
            }
            // Swipe reveals "Categorize"; tapping it opens the category picker.
            .confirmationDialog(
                "Category",
                isPresented: .init(
                    get: { picking != nil },
                    set: { if !$0 { picking = nil } }
                ),
                titleVisibility: .visible,
                presenting: picking
            ) { transaction in
                ForEach(viewModel.categories, id: \.id) { category in
                    Button(category.name) {
                        picking = nil
                        Task { await viewModel.categorize(transaction, as: category) }
                    }
                }
                Button("Cancel", role: .cancel) { picking = nil }
            } message: { transaction in
                Text(transaction.merchant ?? "Transaction")
            }
        }
    }

    private func row(_ transaction: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(transaction.merchant ?? transaction.description ?? "Transaction")
                    .lineLimit(1)
                Text(Self.dateText(transaction.occurredAt))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Text(transaction.amount.formattedExact)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(transaction.amount.amountMinor < 0 ? .primary : Color.green)
        }
    }

    private func undoBar(
        _ action: CategorizeViewModel.Action, _ viewModel: CategorizeViewModel
    ) -> some View {
        HStack {
            Text("Set to \(action.categoryName)")
                .font(.subheadline)
                .lineLimit(1)
            Spacer()
            Button("Undo") { Task { await viewModel.undoLast() } }
                .font(.subheadline.weight(.semibold))
            Button {
                viewModel.dismissUndo()
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal)
        .padding(.vertical, 10)
        .background(.bar)
    }

    /// The contract sends an ISO date string; show it lightly rather than parse
    /// exactly — this is a glanceable list, not a ledger.
    static func dateText(_ iso: String) -> String {
        String(iso.prefix(10))
    }
}
