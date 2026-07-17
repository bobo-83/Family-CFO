import Foundation

/// Drives the shared-photo inbox attach flow (M102): work through the photos the
/// Share Extension dropped in, attaching each to a transaction the user picks.
@MainActor
@Observable
final class SharedInboxViewModel {
    private let api: TransactionDetailAPI

    private(set) var items: [SharedPhotoInbox.Item] = []
    private(set) var transactions: [Components.Schemas.Transaction] = []
    private(set) var isLoading = false
    private(set) var attachingItemID: String?
    var errorMessage: String?

    init(api: TransactionDetailAPI) { self.api = api }

    /// The photo currently being attached (we go one at a time).
    var currentItem: SharedPhotoInbox.Item? { items.first }
    var remaining: Int { items.count }

    func load() async {
        items = SharedPhotoInbox.pending()
        guard !items.isEmpty else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            transactions = try await api.recentTransactions()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func attach(_ item: SharedPhotoInbox.Item, to transactionID: String) async {
        guard attachingItemID == nil else { return }
        attachingItemID = item.id
        defer { attachingItemID = nil }
        guard let data = try? Data(contentsOf: item.imageURL) else {
            errorMessage = "Couldn't read the shared photo."
            return
        }
        do {
            _ = try await api.uploadAttachment(
                transactionID: transactionID, imageData: data, contentType: "image/jpeg")
            // A note the user typed on the share sheet wins over the auto-read one.
            if let note = item.note, !note.isEmpty {
                _ = try await api.update(
                    transactionID: transactionID, categoryID: nil, clearCategory: false,
                    note: note, setNote: true)
            }
            SharedPhotoInbox.remove(item)
            items = SharedPhotoInbox.pending()
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Discard a shared photo without attaching it.
    func skip(_ item: SharedPhotoInbox.Item) {
        SharedPhotoInbox.remove(item)
        items = SharedPhotoInbox.pending()
    }
}
