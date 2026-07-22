import SwiftUI

/// Monthly per-category envelopes on iOS (M118, ADR 0025 parity with the
/// dashboard's Budgets page): each envelope with its month-to-date progress,
/// tap to change the limit, swipe to delete, + to add one for a category that
/// doesn't have one yet.
struct BudgetsView: View {
    // @State so the first instance survives parent re-renders — OverviewView
    // builds this destination inline, and a plain reference property would be
    // silently replaced by a fresh, never-loaded model (same bug as the AI
    // runtime screen, user report 2026-07-22).
    @State var viewModel: BudgetsViewModel
    @State private var addingBudget = false
    @State private var editing: Components.Schemas.Budget?

    var body: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            if viewModel.budgets.isEmpty && !viewModel.isLoading {
                Text("No envelopes yet. Add one with + to cap a category's monthly spending.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            ForEach(viewModel.budgets, id: \.id) { budget in
                budgetRow(budget)
                    .contentShape(Rectangle())
                    .onTapGesture { editing = budget }
                    .swipeActions(edge: .trailing) {
                        Button(role: .destructive) {
                            Task { await viewModel.delete(budget) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
            }
        }
        .navigationTitle("Budgets")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { addingBudget = true } label: {
                    Label("Add budget", systemImage: "plus")
                }
            }
        }
        .sheet(isPresented: $addingBudget) {
            BudgetFormSheet(
                title: "New budget",
                categories: viewModel.unbudgetedCategories,
                initialLimit: nil
            ) { categoryID, limitMinor in
                await viewModel.create(categoryID: categoryID, limitMinor: limitMinor)
            }
        }
        .sheet(item: $editing) { budget in
            BudgetFormSheet(
                title: budget.categoryName,
                categories: nil,
                initialLimit: Double(budget.limit.amountMinor) / 100
            ) { _, limitMinor in
                await viewModel.updateLimit(budget, limitMinor: limitMinor)
            }
        }
        .task { await viewModel.load() }
    }

    private func budgetRow(_ budget: Components.Schemas.Budget) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(budget.categoryName).lineLimit(1)
                Spacer()
                Text("\(budget.spent.formattedExact) of \(budget.limit.formattedExact)")
                    .font(.subheadline.weight(.medium))
                    .monospacedDigit()
            }
            ProgressView(value: min(Double(budget.percentUsed), 100), total: 100)
                .tint(Self.statusColor(budget.status))
            Text(Self.statusLine(budget))
                .font(.caption)
                .foregroundStyle(budget.status == .over ? Color.red : Color.secondary)
        }
        .padding(.vertical, 2)
    }

    static func statusColor(_ status: Components.Schemas.Budget.StatusPayload) -> Color {
        switch status {
        case .under: return .green
        case .warning: return .orange
        case .over: return .red
        }
    }

    static func statusLine(_ budget: Components.Schemas.Budget) -> String {
        switch budget.status {
        case .over:
            return "Over by \(Components.Schemas.Money(amountMinor: -budget.remaining.amountMinor, currency: budget.remaining.currency).formattedExact)"
        case .warning:
            return "\(budget.remaining.formattedExact) left · \(budget.percentUsed)% used"
        case .under:
            return "\(budget.remaining.formattedExact) left this month"
        }
    }
}

/// Create an envelope (category + limit) or change an existing one's limit
/// (category fixed, so `categories` is nil).
private struct BudgetFormSheet: View {
    let title: String
    let categories: [Components.Schemas.Category]?
    let initialLimit: Double?
    let onSave: (String, Int64) async -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var categoryID = ""
    @State private var limit: Double?

    var body: some View {
        NavigationStack {
            Form {
                if let categories {
                    Picker("Category", selection: $categoryID) {
                        Text("Choose…").tag("")
                        ForEach(categories, id: \.id) { category in
                            Text(category.name).tag(category.id)
                        }
                    }
                }
                TextField(
                    "Monthly limit", value: $limit, format: .currency(code: "USD")
                )
                .keyboardType(.decimalPad)
            }
            .navigationTitle(title)
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let minor = Int64(((limit ?? 0) * 100).rounded())
                        let category = categoryID
                        dismiss()
                        Task { await onSave(category, minor) }
                    }
                    .disabled((limit ?? 0) <= 0 || (categories != nil && categoryID.isEmpty))
                }
            }
            .onAppear { limit = initialLimit }
        }
    }
}

/// `Budget` carries a stable `id`; conforming lets it drive `.sheet(item:)`.
extension Components.Schemas.Budget: Identifiable {}
