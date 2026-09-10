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
    private var isEmpty: Bool { Self.isEmpty(spending) }
    static func isEmpty(_ spending: Components.Schemas.SpendingByCategory) -> Bool {
        spending.total.amountMinor == 0 && !spending.total.isPartial
    }
    private var monthTotal: Components.Schemas.QualifiedMoney { spending.total }
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
                    Text(verbatim: monthTotal.formatted).font(.headline)
                        .accessibilityLabel(monthTotal.accessibilityDescription)
                }
                .padding(.bottom, 2)
                if let disclosure = monthTotal.partialDisclosure {
                    Label(disclosure, systemImage: "exclamationmark.circle")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Divider()
                if spending.categories == nil {
                    Text("Spending categories · \(unavailableValueText)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                categoryRows
                uncategorizedRow
            }
        }
    }

    private var categoryRows: some View {
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
                        Text(verbatim: entry.categoryName).font(.subheadline).lineLimit(1)
                            .foregroundStyle(.primary)
                        Spacer()
                        Text(verbatim: entry.amount.formatted)
                            .font(.subheadline.weight(.medium)).foregroundStyle(.primary)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                    }
                    if let disclosure = entry.amount.partialDisclosure {
                        Text(disclosure).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private var uncategorizedRow: some View {
        if spending.uncategorized.amountMinor > 0 || spending.uncategorized.isPartial {
            Divider()
            NavigationLink {
                CategorySpendingDetailView(
                    categoryID: nil,
                    categoryName: String(localized: "Uncategorized"),
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
                        Text(verbatim: spending.uncategorized.formatted)
                            .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
                    }
                    if let disclosure = spending.uncategorized.partialDisclosure {
                        Text(disclosure).font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Text("Tap to sort these in.").font(.caption2).foregroundStyle(.tertiary)
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

}
