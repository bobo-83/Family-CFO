import SwiftUI

/// Quick transaction categorization (M91): the uncategorized transactions in a
/// list, each swiped to assign a category, with an undo for the last one.
/// Adults-only — categorizing changes household money data.
struct CategorizeView: View {
    @Environment(AppModel.self) private var model
    @State private var viewModel: CategorizeViewModel?
    @State private var picking: PickTarget?
    @State private var newCategoryName = ""
    @State private var renamingCategory: Components.Schemas.Category?
    @State private var renameText = ""

    /// Identifiable wrapper so a tapped transaction can drive `.sheet(item:)`.
    private struct PickTarget: Identifiable {
        let txn: Components.Schemas.Transaction
        var id: String { txn.id }
    }
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
            .navigationTitle("Categories")
            .safeAreaInset(edge: .bottom) {
                SyncStatusFooter(status: model.syncStatus)
                    .padding(.vertical, 6)
            }
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
                Text("Create a category to sort transactions into.")
            }
            .alert("Rename category", isPresented: .init(
                get: { renamingCategory != nil },
                set: { if !$0 { renamingCategory = nil } }
            )) {
                TextField("Name", text: $renameText).textInputAutocapitalization(.words)
                Button("Save") { confirmRenameCategory() }
                Button("Cancel", role: .cancel) { renamingCategory = nil }
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
        if let errorMessage = viewModel.errorMessage,
            viewModel.transactions.isEmpty, viewModel.categories.isEmpty {
            ContentUnavailableView {
                Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
            } description: {
                Text(errorMessage)
            } actions: {
                Button("Retry") { Task { await viewModel.load() } }
                    .buttonStyle(.borderedProminent)
            }
        } else {
            VStack(spacing: 0) {
                List {
                    if let errorMessage = viewModel.errorMessage {
                        Label(errorMessage, systemImage: "exclamationmark.triangle")
                            .font(.caption)
                            .foregroundStyle(.red)
                    }

                    Section("To categorize") {
                        if viewModel.transactions.isEmpty {
                            Label("All caught up — every transaction has a category.",
                                systemImage: "checkmark.circle")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(viewModel.transactions, id: \.id) { transaction in
                                transactionLink(transaction, viewModel)
                                    .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                                        Button {
                                            picking = PickTarget(txn: transaction)
                                        } label: {
                                            Label("Categorize", systemImage: "tag")
                                        }
                                        .tint(.accentColor)
                                    }
                            }
                        }
                    }

                    // The full category list lives here too, so this screen manages
                    // categories (rename / delete) — not just assigns them.
                    Section("Categories") {
                        if viewModel.categories.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Label(
                                    "No categories yet. Add a starter set, or make your own.",
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
                        } else {
                            ForEach(viewModel.categories, id: \.id) { category in
                                categoryRow(category, viewModel)
                            }
                        }
                    }
                }
                .refreshable {
                    await viewModel.sync()
                    model.syncStatus.markSynced()
                }

                if let action = viewModel.lastAction {
                    undoBar(action, viewModel)
                }
            }
            // Swipe reveals "Categorize"; tapping it opens the searchable picker.
            .sheet(item: $picking) { target in
                CategoryPickerSheet(
                    title: target.txn.merchant ?? target.txn.description ?? "Transaction",
                    categories: viewModel.categories,
                    currentCategoryID: target.txn.categoryId,
                    onSelect: { newID in
                        guard let id = newID,
                            let category = viewModel.categories.first(where: { $0.id == id })
                        else { return }
                        Task { await viewModel.categorize(target.txn, as: category) }
                    },
                    onCreate: { name in
                        newCategoryName = name
                        creatingCategoryFor = CategoryCreationContext(transaction: target.txn)
                    },
                    onDelete: { category in
                        Task { await viewModel.deleteCategory(id: category.id) }
                    }
                )
            }
        }
    }

    /// A managed category row: swipe or long-press to rename or delete it.
    @ViewBuilder private func categoryRow(
        _ category: Components.Schemas.Category, _ viewModel: CategorizeViewModel
    ) -> some View {
        HStack(spacing: 12) {
            Image(systemName: CategoryVisuals.icon(for: category.name))
                .foregroundStyle(.secondary).frame(width: 24)
            Text(category.name)
            Spacer()
        }
        .contentShape(Rectangle())
        .swipeActions(edge: .trailing) {
            Button(role: .destructive) {
                Task { await viewModel.deleteCategory(id: category.id) }
            } label: { Label("Delete", systemImage: "trash") }
            Button { startRename(category) } label: { Label("Rename", systemImage: "pencil") }
                .tint(.blue)
        }
        .contextMenu {
            Button { startRename(category) } label: { Label("Rename", systemImage: "pencil") }
            Button(role: .destructive) {
                Task { await viewModel.deleteCategory(id: category.id) }
            } label: { Label("Delete", systemImage: "trash") }
        }
    }

    private func startRename(_ category: Components.Schemas.Category) {
        renameText = category.name
        renamingCategory = category
    }

    private func confirmRenameCategory() {
        guard let category = renamingCategory, let viewModel else { return }
        let name = renameText
        renamingCategory = nil
        Task { await viewModel.renameCategory(id: category.id, to: name) }
    }

    /// Tap the row to open the shared detail screen (category, note, check photo);
    /// swipe still offers the quick category picker. Adding a note or photo doesn't
    /// move the transaction, but recategorizing there should refresh this list.
    @ViewBuilder private func transactionLink(
        _ transaction: Components.Schemas.Transaction, _ viewModel: CategorizeViewModel
    ) -> some View {
        if let api = model.transactionDetail {
            NavigationLink {
                TransactionDetailView(
                    viewModel: TransactionDetailViewModel(
                        transaction: transaction, api: api,
                        onChange: { await viewModel.load() }))
            } label: {
                row(transaction)
            }
        } else {
            row(transaction)
        }
    }

    private func row(_ transaction: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(transaction.merchant ?? transaction.description ?? "Transaction")
                    .lineLimit(2)
                if let note = transaction.note, !note.isEmpty {
                    Label(note, systemImage: "note.text")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if let flow = transaction.accountFlow {
                    Text(flow)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if let detail = transaction.rawDetail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
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
            Text(undoText(action))
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

    /// Names the batch so a bulk assignment isn't a silent surprise.
    private func undoText(_ action: CategorizeViewModel.Action) -> String {
        if action.count > 1 {
            return "Set \(action.count) “\(action.merchant)” to \(action.categoryName)"
        }
        return "Set to \(action.categoryName)"
    }

    /// The contract sends an ISO date string; show it lightly rather than parse
    /// exactly — this is a glanceable list, not a ledger.
    static func dateText(_ iso: String) -> String {
        String(iso.prefix(10))
    }
}
