import SwiftUI
import UIKit

/// After you share a photo into Family CFO (M102), this sheet appears so you can
/// attach it to a transaction — searchable, one photo at a time. The photo is
/// then uploaded and auto-described just like one you'd added inside the app.
struct SharedInboxAttachView: View {
    @State var viewModel: SharedInboxViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var search = ""

    private var filtered: [Components.Schemas.Transaction] {
        let term = search.trimmingCharacters(in: .whitespaces)
        guard !term.isEmpty else { return viewModel.transactions }
        return viewModel.transactions.filter {
            ($0.merchant ?? $0.description ?? "").localizedCaseInsensitiveContains(term)
        }
    }

    var body: some View {
        NavigationStack {
            Group {
                if let item = viewModel.currentItem {
                    content(item)
                } else {
                    // Nothing left to attach — close.
                    Color.clear.onAppear { dismiss() }
                }
            }
            .navigationTitle("Attach shared photo")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Later") { dismiss() }
                }
            }
        }
        .task { await viewModel.load() }
    }

    @ViewBuilder private func content(_ item: SharedPhotoInbox.Item) -> some View {
        List {
            Section {
                if let image = SharedPhotoInbox.image(for: item) {
                    Image(uiImage: image)
                        .resizable().scaledToFit()
                        .frame(maxHeight: 200).frame(maxWidth: .infinity)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
                if let note = item.note, !note.isEmpty {
                    Label(note, systemImage: "text.quote")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } footer: {
                Text(
                    viewModel.remaining > 1
                        ? "Pick the transaction this belongs to. \(viewModel.remaining) photos to file."
                        : "Pick the transaction this belongs to.")
            }
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            Section("Transactions") {
                if viewModel.isLoading && viewModel.transactions.isEmpty {
                    ProgressView()
                }
                ForEach(filtered, id: \.id) { txn in
                    Button {
                        Task { await viewModel.attach(item, to: txn.id) }
                    } label: {
                        transactionRow(txn)
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.attachingItemID != nil)
                }
            }
        }
        .searchable(text: $search, prompt: "Search transactions")
        .overlay {
            if viewModel.attachingItemID == item.id {
                ProgressView("Attaching…")
                    .padding().background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
        .safeAreaInset(edge: .bottom) {
            Button(role: .destructive) {
                viewModel.skip(item)
            } label: {
                Label("Discard this photo", systemImage: "trash")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .padding()
            .disabled(viewModel.attachingItemID != nil)
        }
    }

    private func transactionRow(_ txn: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text(txn.merchant ?? txn.description ?? "Transaction").lineLimit(1)
                Text(String(txn.occurredAt.prefix(10)))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(txn.amount.formattedExact)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(txn.amount.amountMinor > 0 ? Color.green : .primary)
        }
    }
}
