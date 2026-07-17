import PhotosUI
import SwiftUI

/// The shared transaction detail screen (M100). Every transaction row across the
/// app pushes to this one screen so category, note, and check-photo behave
/// identically everywhere. Attaching a check photo parses a description off the
/// image and pre-fills the (editable) note.
struct TransactionDetailView: View {
    @State var viewModel: TransactionDetailViewModel
    @State private var pickingCategory = false
    @State private var photoItem: PhotosPickerItem?
    @State private var showingCamera = false
    @State private var viewingImage = false
    @FocusState private var noteFocused: Bool

    var body: some View {
        Form {
            headerSection
            categorySection
            noteSection
            attachmentSection
        }
        .navigationTitle("Details")
        .navigationBarTitleDisplayMode(.inline)
        .keyboardDoneButton()
        .task { await viewModel.load() }
        .sheet(isPresented: $pickingCategory) {
            CategoryPickerSheet(
                title: viewModel.title,
                categories: viewModel.categories,
                currentCategoryID: viewModel.transaction.categoryId,
                onSelect: { newID in Task { await viewModel.setCategory(newID) } },
                onDelete: { category in Task { await viewModel.deleteCategory(category.id) } })
        }
        .sheet(isPresented: $showingCamera) {
            CameraPicker { image in Task { await viewModel.uploadAttachment(image) } }
                .ignoresSafeArea()
        }
        .sheet(isPresented: $viewingImage) {
            if let image = viewModel.attachmentImage {
                ImageViewer(image: image)
            }
        }
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            Task {
                if let data = try? await item.loadTransferable(type: Data.self),
                    let image = UIImage(data: data) {
                    await viewModel.uploadAttachment(image)
                }
                photoItem = nil
            }
        }
        .onChange(of: noteFocused) { _, focused in
            if !focused { Task { await viewModel.saveNote() } }
        }
        .onDisappear { Task { await viewModel.saveNote() } }
        .alert(
            "Something went wrong",
            isPresented: Binding(
                get: { viewModel.errorMessage != nil },
                set: { if !$0 { viewModel.errorMessage = nil } })
        ) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(viewModel.errorMessage ?? "")
        }
    }

    private var headerSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 4) {
                Text(viewModel.title).font(.headline)
                HStack {
                    Text(viewModel.transaction.amount.formattedExact)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(amountColor)
                    Spacer()
                    Text(String(viewModel.transaction.occurredAt.prefix(10)))
                        .font(.subheadline).foregroundStyle(.secondary)
                }
                if let source = sourceLine {
                    Text(source).font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.vertical, 2)
        }
    }

    private var categorySection: some View {
        Section("Category") {
            Button {
                pickingCategory = true
            } label: {
                HStack {
                    Image(systemName: CategoryVisuals.icon(for: viewModel.categoryName ?? ""))
                        .foregroundStyle(.secondary).frame(width: 24)
                    Text(viewModel.categoryName ?? "Uncategorized")
                        .foregroundStyle(viewModel.categoryName == nil ? .secondary : .primary)
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption).foregroundStyle(.tertiary)
                }
            }
            .tint(.primary)
        }
    }

    private var noteSection: some View {
        Section {
            if viewModel.parsedNoteFromImage {
                Label(
                    "We read this from the photo — edit it if it's not quite right.",
                    systemImage: "sparkles"
                )
                .font(.caption).foregroundStyle(.secondary)
            }
            TextField(
                "Add a note (e.g. what this check was for)",
                text: $viewModel.noteDraft, axis: .vertical
            )
            .lineLimit(1...6)
            .focused($noteFocused)
            if viewModel.isSavingNote {
                Label("Saving…", systemImage: "arrow.triangle.2.circlepath")
                    .font(.caption).foregroundStyle(.secondary)
            }
        } header: {
            Text("Note")
        } footer: {
            Text("Saved automatically.")
        }
    }

    @ViewBuilder private var attachmentSection: some View {
        Section {
            if let image = viewModel.attachmentImage {
                Button { viewingImage = true } label: {
                    Image(uiImage: image)
                        .resizable().scaledToFit()
                        .frame(maxHeight: 220)
                        .frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            }
            if viewModel.isUploading {
                HStack {
                    ProgressView()
                    Text("Uploading and reading the photo…")
                        .font(.subheadline).foregroundStyle(.secondary)
                }
            }
            // Each source is its own row so their tap targets never overlap.
            PhotosPicker(selection: $photoItem, matching: .images) {
                Label(
                    viewModel.hasAttachment ? "Replace from library" : "Choose from library",
                    systemImage: "photo.on.rectangle")
            }
            .disabled(viewModel.isUploading)
            Button {
                pasteImage()
            } label: {
                Label("Paste from clipboard", systemImage: "doc.on.clipboard")
            }
            .buttonStyle(.borderless)
            .disabled(viewModel.isUploading)
            Button {
                showingCamera = true
            } label: {
                Label("Take photo", systemImage: "camera")
            }
            .buttonStyle(.borderless)
            .disabled(viewModel.isUploading)
            if viewModel.hasAttachment {
                Button(role: .destructive) {
                    Task { await viewModel.deleteAttachment() }
                } label: {
                    Label("Remove photo", systemImage: "trash")
                }
                .buttonStyle(.borderless)
                .disabled(viewModel.isUploading)
            }
        } header: {
            Text("Check / receipt photo")
        } footer: {
            Text("Attach a photo of a check or receipt. We'll read a short description off it and fill in the note above for you to edit.")
        }
    }

    /// Grab an image straight off the clipboard — handy when you've copied a check
    /// photo from Photos, Messages, or Mail. Shares the reader with every other
    /// paste-a-statement input (M114, ADR 0028) so they all behave identically.
    private func pasteImage() {
        let vm = viewModel
        ClipboardImage.read { contents in
            switch contents {
            case .image(let image):
                Task { await vm.uploadAttachment(image) }
            case .pdf:
                vm.errorMessage =
                    "That's a PDF — attachments here are photos. Take a screenshot of it and paste that instead."
            case .none:
                vm.errorMessage = "There's no image on your clipboard to paste."
            }
        }
    }

    private var amountColor: Color {
        // Match the rest of the app: only genuine money back is green. A transfer
        // leg that happens to be positive (a payment landing on a card) is neutral.
        let category = (viewModel.transaction.category ?? "").lowercased()
        let isTransfer = category.contains("transfer")
        return viewModel.transaction.amount.amountMinor > 0 && !isTransfer ? .green : .primary
    }

    private var sourceLine: String? {
        switch (viewModel.transaction.institution, viewModel.transaction.accountName) {
        case let (institution?, account?): return "\(institution) · \(account)"
        case let (institution?, nil): return institution
        case let (nil, account?): return account
        default: return nil
        }
    }
}

/// Full-screen pinch-to-zoom look at the attached image.
private struct ImageViewer: View {
    let image: UIImage
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView([.horizontal, .vertical]) {
                Image(uiImage: image)
                    .resizable().scaledToFit()
                    .frame(maxWidth: .infinity)
            }
            .navigationTitle("Photo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
