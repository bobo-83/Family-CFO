import Foundation
import Observation

/// Swipe-to-categorize the uncategorized transactions (M91).
///
/// Assigning is optimistic — the row leaves the list at once — but if the server
/// refuses, the row comes BACK in its original place, because a list that hides a
/// transaction the box still shows as uncategorized is a lie the user would act
/// on. The last assignment is undoable: undo clears the category server-side and
/// restores the row.
@MainActor
@Observable
final class CategorizeViewModel {
    private(set) var transactions: [Components.Schemas.Transaction] = []
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var isLoading = false
    var errorMessage: String?

    /// The most recent assignment, offered for undo until the next action.
    private(set) var lastAction: Action?

    /// One assignment — possibly of several transactions at once, when they share
    /// a merchant (M91b). Undo reverts every one of them.
    struct Action: Equatable {
        struct Item: Equatable {
            let transaction: Components.Schemas.Transaction
            let index: Int
        }
        let items: [Item]
        let categoryName: String
        let merchant: String

        var count: Int { items.count }
    }

    private let api: CategorizeAPI

    init(api: CategorizeAPI) {
        self.api = api
    }

    /// The name two transactions must share to be categorized together — what the
    /// user actually sees in the row (merchant, or description when there's no
    /// merchant), normalized. nil means "don't bulk" (nothing to match on).
    static func matchKey(_ transaction: Components.Schemas.Transaction) -> String? {
        let name = transaction.merchant ?? transaction.description
        let trimmed = name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return trimmed.isEmpty ? nil : trimmed.lowercased()
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let txns = api.uncategorized()
            async let cats = api.categories()
            transactions = try await txns
            categories = try await cats
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Pull-to-refresh: sync the banks first, then reload — same gesture as every
    /// other tab (M103), and a sync brings in new uncategorized transactions.
    func sync() async {
        do {
            try await api.syncBanks()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
        await load()
    }

    /// Categorize the tapped transaction AND every other uncategorized one with
    /// the same merchant (M91b) — so filing "Starbucks" once files them all. Each
    /// is set on the server; a row that the server refuses comes back in place,
    /// and only the ones that actually took are recorded for undo.
    func categorize(
        _ transaction: Components.Schemas.Transaction,
        as category: Components.Schemas.Category
    ) async {
        let key = Self.matchKey(transaction)
        // The tapped row, plus its merchant-mates (or just itself when there's no
        // merchant to match on). Highest index first, so removals don't shift the
        // indices of ones still to be removed.
        let targets: [(transaction: Components.Schemas.Transaction, index: Int)] =
            transactions.enumerated()
            .filter { _, txn in
                txn.id == transaction.id || (key != nil && Self.matchKey(txn) == key)
            }
            .map { (transaction: $0.element, index: $0.offset) }
            .sorted { $0.index > $1.index }
        guard !targets.isEmpty else { return }

        for target in targets {
            transactions.remove(at: target.index)
        }

        var succeeded: [Action.Item] = []
        var failed = false
        for target in targets {
            do {
                try await api.setCategory(transactionID: target.transaction.id, categoryID: category.id)
                succeeded.append(Action.Item(transaction: target.transaction, index: target.index))
            } catch {
                // Put this one back where it was; it didn't take.
                transactions.insert(target.transaction, at: min(target.index, transactions.count))
                failed = true
                errorMessage = ChatViewModel.describe(error)
            }
        }

        if !failed { errorMessage = nil }
        if !succeeded.isEmpty {
            lastAction = Action(
                items: succeeded.sorted { $0.index < $1.index },
                categoryName: category.name,
                merchant: transaction.merchant ?? transaction.description ?? "transactions")
        }
    }

    /// Create a category and add it to the local list (M91a). Returns it so the
    /// caller can immediately categorize the transaction that prompted it.
    @discardableResult
    func createCategory(named name: String) async -> Components.Schemas.Category? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        // If it already exists locally, reuse it rather than round-trip to a 409.
        if let existing = categories.first(where: { $0.name.caseInsensitiveCompare(trimmed) == .orderedSame }) {
            return existing
        }
        do {
            let category = try await api.createCategory(name: trimmed)
            categories.append(category)
            categories.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
            errorMessage = nil
            return category
        } catch {
            errorMessage = Self.describe(error)
            return nil
        }
    }

    /// Delete a category (M96). The server un-categorizes its transactions, so we
    /// reload to pull them back into the uncategorized list.
    func deleteCategory(id: String) async {
        categories.removeAll { $0.id == id }
        do {
            try await api.deleteCategory(id: id)
            errorMessage = nil
        } catch {
            errorMessage = Self.describe(error)
        }
        await load()
    }

    private(set) var isAddingStarters = false

    /// Create the starter categories that don't already exist (M91a), so a
    /// household with none can get going in one tap. Resilient: an already-present
    /// name is skipped, and a failure on one leaves the rest (and reports it)
    /// rather than aborting the batch.
    func addStarterCategories() async {
        guard !isAddingStarters else { return }
        isAddingStarters = true
        defer { isAddingStarters = false }

        var lastError: String?
        for name in CategoryDefaults.starter {
            if categories.contains(where: { $0.name.caseInsensitiveCompare(name) == .orderedSame }) {
                continue
            }
            do {
                let category = try await api.createCategory(name: name)
                categories.append(category)
            } catch is CategorizeError {
                // Exists on the server but not in our local list — treat as done.
                continue
            } catch {
                lastError = Self.describe(error)
            }
        }
        categories.sort { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        errorMessage = lastError
    }

    private static func describe(_ error: Error) -> String {
        if let e = error as? CategorizeError { return e.errorDescription ?? "\(e)" }
        return ChatViewModel.describe(error)
    }

    func undoLast() async {
        guard let action = lastAction else { return }
        lastAction = nil
        var failed = false
        // Restore low index first, so each insert lands at the right spot.
        for item in action.items.sorted(by: { $0.index < $1.index }) {
            do {
                try await api.setCategory(transactionID: item.transaction.id, categoryID: nil)
                transactions.insert(item.transaction, at: min(item.index, transactions.count))
            } catch {
                failed = true
                errorMessage = ChatViewModel.describe(error)
            }
        }
        if !failed { errorMessage = nil }
    }

    func dismissUndo() {
        lastAction = nil
    }
}
