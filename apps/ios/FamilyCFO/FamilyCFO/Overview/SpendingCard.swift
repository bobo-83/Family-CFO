import SwiftUI

/// This month's spend per category with a proportion bar (M94) and a total. The
/// month is chosen by the Overview's global month picker (M96); each row drills
/// into its transactions, and a recategorize there refreshes the whole Overview.
struct SpendingCard: View {
    let spending: Components.Schemas.SpendingByCategory
    let api: HouseholdAPI
    let categorizeAPI: CategorizeAPI
    /// Reload the Overview after an in-place recategorize.
    let onChanged: () async -> Void

    private var categories: [Components.Schemas.CategorySpend] { spending.categories ?? [] }
    private var isEmpty: Bool {
        categories.isEmpty && spending.uncategorized.amountMinor == 0
    }
    private var monthTotal: Components.Schemas.Money {
        .init(
            amountMinor: spending.categorizedTotal.amountMinor + spending.uncategorized.amountMinor,
            currency: spending.categorizedTotal.currency)
    }

    var body: some View {
        Card("Spending · \(spending.monthLabel)", systemImage: "chart.pie") {
            if isEmpty {
                Text("Nothing spent in \(spending.monthLabel).")
                    .font(.caption).foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 6)
            } else {
                HStack {
                    Text("Total spent").font(.subheadline).foregroundStyle(.secondary)
                    Spacer()
                    Text(monthTotal.formatted).font(.headline)
                }
                .padding(.bottom, 2)
                Divider()
                categoryRows
                uncategorizedRow
            }
        }
    }

    private var categoryRows: some View {
        let maxAmount = categories.map(\.amount.amountMinor).max() ?? 1
        // Show every category (the API already returns them sorted, biggest
        // first). Capping at the top N silently dropped smaller ones, so the
        // rows didn't add up to the total spent.
        return ForEach(categories, id: \.categoryId) { entry in
            NavigationLink {
                CategorySpendingDetailView(
                    categoryID: entry.categoryId,
                    categoryName: entry.categoryName,
                    month: spending.month,
                    monthLabel: spending.monthLabel,
                    currency: entry.amount.currency,
                    api: api,
                    categorizeAPI: categorizeAPI,
                    onChanged: onChanged)
            } label: {
                VStack(spacing: 3) {
                    HStack {
                        Text(entry.categoryName).font(.subheadline).lineLimit(1)
                            .foregroundStyle(.primary)
                        Spacer()
                        Text(entry.amount.formatted)
                            .font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                    }
                    GeometryReader { geo in
                        Capsule()
                            .fill(.tint)
                            .frame(
                                width: geo.size.width * proportion(entry.amount.amountMinor, of: maxAmount),
                                height: 4)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(height: 4)
                }
            }
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private var uncategorizedRow: some View {
        if spending.uncategorized.amountMinor > 0 {
            Divider()
            NavigationLink {
                CategorySpendingDetailView(
                    categoryID: nil,
                    categoryName: "Uncategorized",
                    month: spending.month,
                    monthLabel: spending.monthLabel,
                    currency: spending.uncategorized.currency,
                    api: api,
                    categorizeAPI: categorizeAPI,
                    onChanged: onChanged)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text("Uncategorized").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Text(spending.uncategorized.formatted)
                            .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                    }
                    Text("Tap to sort these in.").font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)
        }
    }

    private func proportion(_ amount: Int64, of maxAmount: Int64) -> CGFloat {
        guard maxAmount > 0 else { return 0 }
        return CGFloat(max(0, amount)) / CGFloat(maxAmount)
    }
}
