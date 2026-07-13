import SwiftUI

/// Quick transaction categorization (M91): the uncategorized transactions in a
/// list, each swiped to assign a category, with an undo for the last one.
/// Adults-only — categorizing changes household money data.
struct CategorizeView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: CategorizeViewModel?
    @State private var picking: Components.Schemas.Transaction?
    @State private var newCategoryName = ""
    /// When set, the "New category" prompt is shown; the transaction (if any) is
    /// categorized with the created category once it's made.
    @State private var creatingCategoryFor: CategoryCreationContext?

    /// nil transaction = created from the toolbar (just add it); a transaction
    /// = created mid-categorize (add it, then assign it to that transaction).
    private struct CategoryCreationContext: Identifiable {
        let id = UUID()
        let transaction: Components.Schemas.Transaction?
    }

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
            .toolbar {
                if let viewModel {
                    ToolbarItem(placement: .primaryAction) {
                        Menu {
                            Button {
                                newCategoryName = ""
                                creatingCategoryFor = CategoryCreationContext(transaction: nil)
                            } label: {
                                Label("New category…", systemImage: "plus")
                            }
                            Button {
                                Task { await viewModel.addStarterCategories() }
                            } label: {
                                Label("Add starter categories", systemImage: "square.stack.3d.up")
                            }
                            .disabled(viewModel.isAddingStarters)
                        } label: {
                            Label("Add category", systemImage: "plus")
                        }
                    }
                }
            }
            .alert("New category", isPresented: .init(
                get: { creatingCategoryFor != nil },
                set: { if !$0 { creatingCategoryFor = nil } }
            )) {
                TextField("Name (e.g. Groceries)", text: $newCategoryName)
                    .textInputAutocapitalization(.words)
                Button("Create") { confirmCreateCategory() }
                Button("Cancel", role: .cancel) { creatingCategoryFor = nil }
            } message: {
                Text("Create a category to sort transactions into. Manage them fully on the dashboard.")
            }
        }
        .task {
            if viewModel == nil, let api = model.categorize {
                viewModel = CategorizeViewModel(api: api)
            }
            await viewModel?.load()
        }
    }

    private func confirmCreateCategory() {
        guard let context = creatingCategoryFor, let viewModel else { return }
        let name = newCategoryName
        creatingCategoryFor = nil
        Task {
            guard let category = await viewModel.createCategory(named: name) else { return }
            // Created mid-categorize: assign the transaction that prompted it.
            if let transaction = context.transaction {
                await viewModel.categorize(transaction, as: category)
            }
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
                    // No categories yet? Offer the one-tap starter set right here,
                    // so the screen isn't a dead end (M91a). Custom stays available
                    // via the + menu and the per-transaction picker.
                    if viewModel.categories.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            Label(
                                "No categories yet. Add a starter set, or make your own — full management is on the dashboard.",
                                systemImage: "info.circle"
                            )
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            Button {
                                Task { await viewModel.addStarterCategories() }
                            } label: {
                                if viewModel.isAddingStarters {
                                    ProgressView()
                                } else {
                                    Label("Add starter categories", systemImage: "square.stack.3d.up")
                                }
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(viewModel.isAddingStarters)
                        }
                        .padding(.vertical, 4)
                    }
                    ForEach(viewModel.transactions, id: \.id) { transaction in
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
                Button("New category…") {
                    picking = nil
                    newCategoryName = ""
                    creatingCategoryFor = CategoryCreationContext(transaction: transaction)
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
