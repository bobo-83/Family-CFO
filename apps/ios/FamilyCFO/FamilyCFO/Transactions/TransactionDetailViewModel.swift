import SwiftUI

/// Drives the shared transaction-detail screen (M100): category, a free-text
/// note, and a photo attachment (e.g. a check, whose description is parsed off
/// the image and pre-filled into the note for the user to edit).
@MainActor
@Observable
final class TransactionDetailViewModel {
    private let api: TransactionDetailAPI
    /// Called after any change that the presenting list should reflect (e.g. a
    /// recategorize that moves the transaction out of the current category).
    private let onChange: (() async -> Void)?

    /// The latest server state of the transaction — replaced whenever a mutation
    /// returns a fresh copy.
    private(set) var transaction: Components.Schemas.Transaction
    private(set) var categories: [Components.Schemas.Category] = []
    /// The attached image, once loaded or just uploaded.
    private(set) var attachmentImage: UIImage?
    private(set) var isUploading = false
    private(set) var isSavingNote = false
    var errorMessage: String?

    /// Editable note text, bound to the field. Kept separate from the server copy
    /// so typing doesn't fight round-trips; saved on commit.
    var noteDraft: String

    /// Set true by an upload when a description was parsed, so the UI can nudge the
    /// user to review/edit it. Cleared once acknowledged.
    private(set) var parsedNoteFromImage = false

    init(
        transaction: Components.Schemas.Transaction,
        api: TransactionDetailAPI,
        onChange: (() async -> Void)? = nil
    ) {
        self.transaction = transaction
        self.api = api
        self.onChange = onChange
        self.noteDraft = transaction.note ?? ""
    }

    var hasAttachment: Bool { transaction.hasAttachment ?? false }
    var categoryName: String? { transaction.category }

    var title: String { transaction.merchant ?? transaction.description ?? "Transaction" }

    func load() async {
        do {
            categories = try await api.categories()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
        if hasAttachment, attachmentImage == nil {
            await loadAttachment()
        }
    }

    private func loadAttachment() async {
        do {
            if let data = try await api.attachmentImage(transactionID: transaction.id),
                let image = UIImage(data: data) {
                attachmentImage = image
            }
        } catch {
            // Non-fatal: the note/category still work without the thumbnail.
        }
    }

    func setCategory(_ categoryID: String?) async {
        do {
            transaction = try await api.update(
                transactionID: transaction.id,
                categoryID: categoryID,
                clearCategory: categoryID == nil,
                note: nil, setNote: false)
            await onChange?()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Delete a category from the shared picker (long-press). The server
    /// un-categorizes its transactions; reflect that locally and refresh.
    func deleteCategory(_ id: String) async {
        do {
            try await api.deleteCategory(id: id)
            categories = try await api.categories()
            if transaction.categoryId == id {
                var copy = transaction
                copy.categoryId = nil
                copy.category = nil
                transaction = copy
            }
            await onChange?()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Persist the edited note (empty string clears it).
    func saveNote() async {
        let trimmed = noteDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        // Skip a redundant round-trip if unchanged.
        if trimmed == (transaction.note ?? "") { parsedNoteFromImage = false; return }
        isSavingNote = true
        defer { isSavingNote = false }
        do {
            transaction = try await api.update(
                transactionID: transaction.id,
                categoryID: nil, clearCategory: false,
                note: trimmed, setNote: true)
            noteDraft = transaction.note ?? ""
            parsedNoteFromImage = false
            // Refresh the presenting list + month cache so reopening this
            // transaction shows the saved note instead of the stale (blank) copy.
            await onChange?()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func uploadAttachment(_ image: UIImage) async {
        guard let data = image.jpegData(compressionQuality: 0.8) else {
            errorMessage = "Couldn't read that photo."
            return
        }
        isUploading = true
        defer { isUploading = false }
        attachmentImage = image  // optimistic; the parse happens server-side
        let hadNote = !(transaction.note ?? "").isEmpty
        do {
            transaction = try await api.uploadAttachment(
                transactionID: transaction.id, imageData: data, contentType: "image/jpeg")
            // The server parses the check and fills the note when it was empty.
            let newNote = transaction.note ?? ""
            noteDraft = newNote
            parsedNoteFromImage = !hadNote && !newNote.isEmpty
        } catch {
            errorMessage = ChatViewModel.describe(error)
            attachmentImage = nil
        }
    }

    func deleteAttachment() async {
        do {
            try await api.deleteAttachment(transactionID: transaction.id)
            attachmentImage = nil
            var copy = transaction
            copy.hasAttachment = false
            transaction = copy
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    func acknowledgeParsedNote() { parsedNoteFromImage = false }
}
