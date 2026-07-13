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

    struct Action: Equatable {
        let transaction: Components.Schemas.Transaction
        let index: Int
        let categoryName: String
    }

    private let api: CategorizeAPI

    init(api: CategorizeAPI) {
        self.api = api
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

    func categorize(
        _ transaction: Components.Schemas.Transaction,
        as category: Components.Schemas.Category
    ) async {
        guard let index = transactions.firstIndex(where: { $0.id == transaction.id }) else {
            return
        }
        transactions.remove(at: index)
        do {
            try await api.setCategory(transactionID: transaction.id, categoryID: category.id)
            lastAction = Action(transaction: transaction, index: index, categoryName: category.name)
            errorMessage = nil
        } catch {
            // Put it back exactly where it was; the categorization didn't take.
            transactions.insert(transaction, at: min(index, transactions.count))
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func undoLast() async {
        guard let action = lastAction else { return }
        lastAction = nil
        do {
            try await api.setCategory(transactionID: action.transaction.id, categoryID: nil)
            // Restore it to where it was, so the list looks like it did pre-action.
            let at = min(action.index, transactions.count)
            transactions.insert(action.transaction, at: at)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func dismissUndo() {
        lastAction = nil
    }
}
