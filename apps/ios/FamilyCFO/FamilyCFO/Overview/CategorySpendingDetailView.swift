import SwiftUI

/// The transactions behind one category's monthly spend (M94) — tap a category
/// on the Overview's Spending card to see what's in it. Filters to the same
/// window the card summed, so the total here reconciles with the card.
struct CategorySpendingDetailView: View {
    let categoryID: String
    let categoryName: String
    let month: String
    let monthLabel: String
    let currency: String
    let api: HouseholdAPI

    @State private var items: [Components.Schemas.Transaction] = []
    @State private var total: Components.Schemas.Money?
    @State private var isLoading = true
    @State private var errorMessage: String?

    var body: some View {
        Group {
            if let errorMessage, items.isEmpty {
                ContentUnavailableView {
                    Label("Can't load transactions", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(errorMessage)
                } actions: {
                    Button("Retry") { Task { await load() } }.buttonStyle(.borderedProminent)
                }
            } else if items.isEmpty && !isLoading {
                ContentUnavailableView(
                    "No transactions",
                    systemImage: "tray",
                    description: Text("Nothing in \(categoryName) for \(monthLabel)."))
            } else {
                List {
                    if let total {
                        Section {
                            LabeledContent("\(monthLabel) total", value: total.formattedExact)
                                .font(.headline)
                        }
                    }
                    Section {
                        ForEach(items, id: \.id) { txn in
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(txn.merchant ?? txn.description ?? "Transaction")
                                        .lineLimit(1)
                                    Text(String(txn.occurredAt.prefix(10)))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                Text(txn.amount.formattedExact)
                                    .font(.subheadline.weight(.medium))
                            }
                        }
                    } header: {
                        Text("\(items.count) transaction\(items.count == 1 ? "" : "s")")
                    }
                }
            }
        }
        .navigationTitle(categoryName)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            let all = try await api.transactions()
            items = CategorySpendingDetail.items(in: all, categoryID: categoryID, month: month)
            total = CategorySpendingDetail.total(items, currency: currency)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
