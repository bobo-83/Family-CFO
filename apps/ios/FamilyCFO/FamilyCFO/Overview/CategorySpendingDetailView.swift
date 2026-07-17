import SwiftUI

/// The transactions behind one category's monthly spend (M94) — tap a category
/// on the Overview's Spending card to see what's in it. Filters to the same
/// window the card summed, so the total here reconciles with the card. Tap a
/// transaction to move it to a different category (M96).
struct CategorySpendingDetailView: View {
    /// nil = the Uncategorized drill-down.
    let categoryID: String?
    let categoryName: String
    let month: String
    let monthLabel: String
    let currency: String
    let api: HouseholdAPI
    let categorizeAPI: CategorizeAPI
    /// Called after a recategorize so the caller (e.g. the Overview card) can refresh.
    var onChanged: (() async -> Void)? = nil

    @Environment(AppModel.self) private var model
    @State private var items: [Components.Schemas.Transaction] = []
    @State private var categories: [Components.Schemas.Category] = []
    @State private var total: Components.Schemas.Money?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var picking: Picking?

    /// Identifiable wrapper so a tapped transaction can drive `.sheet(item:)`.
    private struct Picking: Identifiable {
        let txn: Components.Schemas.Transaction
        var id: String { txn.id }
    }

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
                        ForEach(CategorySpendingDetail.grouped(items)) { displayRow in
                            switch displayRow {
                            case .single(let txn):
                                transactionLink(txn)
                            case .refunded(let purchase, let refund):
                                DisclosureGroup {
                                    transactionLink(purchase)
                                    transactionLink(refund)
                                } label: {
                                    refundedRow(purchase)
                                }
                            }
                        }
                    } header: {
                        Text("\(items.count) transaction\(items.count == 1 ? "" : "s")")
                    } footer: {
                        Text("Tap a transaction to move it to another category. Refunded purchases are grouped — expand to see the refund.")
                    }
                }
            }
        }
        .navigationTitle(categoryName)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .sheet(item: $picking) { pick in
            CategoryPickerSheet(
                title: pick.txn.merchant ?? pick.txn.description ?? "Transaction",
                categories: categories,
                currentCategoryID: pick.txn.categoryId,
                onSelect: { newID in Task { await move(pick.txn, to: newID) } },
                // Categories here are already usage-sorted, so the first is the
                // household's most-used — a sane recommendation for a merchant
                // the keyword heuristic doesn't recognize.
                recommendedFallback: categories.first,
                onDelete: { category in Task { await deleteCategory(id: category.id) } }
            )
        }
    }

    /// Tap a transaction to open the shared detail screen (category, note, check
    /// photo). Recategorizing there moves it out of this category, so refresh both
    /// this list and the Overview card on return.
    @ViewBuilder private func transactionLink(
        _ txn: Components.Schemas.Transaction
    ) -> some View {
        if let detailAPI = model.transactionDetail {
            NavigationLink {
                TransactionDetailView(
                    viewModel: TransactionDetailViewModel(
                        transaction: txn, api: detailAPI,
                        onChange: {
                            model.monthTransactions.invalidate()
                            await load()
                            await onChanged?()
                        }))
            } label: {
                row(txn)
            }
            .buttonStyle(.plain)
        } else {
            Button { picking = Picking(txn: txn) } label: { row(txn) }
                .buttonStyle(.plain)
        }
    }

    private func row(_ txn: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(txn.merchant ?? txn.description ?? "Transaction")
                    .lineLimit(1)
                    .foregroundStyle(.primary)
                if let note = txn.note, !note.isEmpty {
                    Label(note, systemImage: "note.text")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if let flow = txn.accountFlow {
                    Text(flow)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                if let detail = txn.rawDetail {
                    Text(detail)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
                Text(String(txn.occurredAt.prefix(10)))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            // A positive amount here is money coming back — a credit or refund
            // netting against this category. Green it so it reads as money in.
            Text(txn.amount.formattedExact)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(txn.amount.amountMinor > 0 ? Color.green : .primary)
        }
    }

    /// A refunded purchase: the amount struck through with a "Refunded" chip. The
    /// disclosure chevron (added by DisclosureGroup) expands to the two entries.
    private func refundedRow(_ purchase: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(purchase.merchant ?? purchase.description ?? "Transaction")
                    .lineLimit(1)
                    .foregroundStyle(.primary)
                HStack(spacing: 6) {
                    Text(String(purchase.occurredAt.prefix(10)))
                    Text("Refunded")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.green.opacity(0.15))
                        .foregroundStyle(.green)
                        .clipShape(Capsule())
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Text(purchase.amount.formattedExact)
                .font(.subheadline.weight(.medium))
                .strikethrough()
                .foregroundStyle(.secondary)
        }
    }

    private func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            // Read the month's data that Overview loaded (M105) — no fetch. Only if
            // the cache is cold (e.g. opened before Overview ran) do we fetch once.
            let all: [Components.Schemas.Transaction]
            let cats: [Components.Schemas.Category]
            if let hit = model.monthTransactions.cached(month: month) {
                all = hit.transactions
                cats = hit.categories
            } else {
                async let allTxns = api.transactions(month: month)
                async let fetchedCats = categorizeAPI.categories()
                all = try await allTxns
                cats = try await fetchedCats
                model.monthTransactions.store(month: month, transactions: all, categories: cats)
            }
            categories = Self.sortedByUsage(cats, transactions: all)
            items = CategorySpendingDetail.items(in: all, categoryID: categoryID, month: month)
            total = CategorySpendingDetail.total(items, currency: currency)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Most-used categories first (how often each is assigned across all
    /// transactions), alphabetical as a tiebreak — so the picker leads with the
    /// ones you actually reach for.
    static func sortedByUsage(
        _ categories: [Components.Schemas.Category],
        transactions: [Components.Schemas.Transaction]
    ) -> [Components.Schemas.Category] {
        var usage: [String: Int] = [:]
        for txn in transactions {
            if let id = txn.categoryId { usage[id, default: 0] += 1 }
        }
        return categories.sorted { lhs, rhs in
            let l = usage[lhs.id] ?? 0
            let r = usage[rhs.id] ?? 0
            if l != r { return l > r }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    private func move(_ txn: Components.Schemas.Transaction, to newCategoryID: String?) async {
        picking = nil
        do {
            try await categorizeAPI.setCategory(transactionID: txn.id, categoryID: newCategoryID)
            model.monthTransactions.invalidate()  // the month's data changed
            await load()  // the moved transaction leaves this category's list
            await onChanged?()  // let the Overview card refresh its totals
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Delete a category (M96). Its transactions become uncategorized server-side;
    /// reload so this list and the Overview totals reflect that.
    private func deleteCategory(id: String) async {
        do {
            try await categorizeAPI.deleteCategory(id: id)
            model.monthTransactions.invalidate()  // categories + assignments changed
            await load()
            await onChanged?()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
