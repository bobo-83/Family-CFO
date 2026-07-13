import SwiftUI

/// The review queues (M90): one-tap decisions on recurring-bill suggestions and
/// unclassified deposits. Adults-only — both actions change household money data.
/// The view model is owned by the shell so its `pendingCount` drives the tab
/// badge; this screen just reads and mutates it.
struct ReviewView: View {
    @Bindable var viewModel: ReviewViewModel

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.billSuggestions.isEmpty && viewModel.deposits.isEmpty {
                    if let errorMessage = viewModel.errorMessage {
                        ContentUnavailableView {
                            Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
                        } description: {
                            Text(errorMessage)
                        } actions: {
                            Button("Retry") { Task { await viewModel.load() } }
                                .buttonStyle(.borderedProminent)
                        }
                    } else if !viewModel.isLoading {
                        ContentUnavailableView {
                            Label("Nothing to review", systemImage: "checkmark.circle")
                        } description: {
                            Text("New bill suggestions and unclassified deposits show up here.")
                        }
                    } else {
                        ProgressView()
                    }
                } else {
                    queues
                }
            }
            .navigationTitle("Review")
        }
        .task { await viewModel.load() }
    }

    private var queues: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }

            if !viewModel.billSuggestions.isEmpty {
                Section("Recurring bills to confirm") {
                    ForEach(viewModel.billSuggestions, id: \.merchantKey) { suggestion in
                        billRow(suggestion)
                    }
                }
            }

            if !viewModel.deposits.isEmpty {
                Section("Deposits — income?") {
                    ForEach(viewModel.deposits, id: \.transactionId) { deposit in
                        depositRow(deposit)
                    }
                }
            }
        }
        .refreshable { await viewModel.load() }
    }

    private func billRow(_ suggestion: Components.Schemas.BillSuggestion) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(suggestion.name).lineLimit(1)
                Spacer()
                Text(suggestion.amount.formattedExact)
                    .font(.subheadline.weight(.medium))
            }
            Text(
                "\(Self.frequencyText(suggestion.frequency)) · seen \(suggestion.occurrences)× · "
                    + "next \(String(suggestion.nextDueDate.prefix(10)))"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.confirmBill(suggestion) }
            } label: {
                Label("Add bill", systemImage: "checkmark")
            }
            .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.dismissBill(suggestion) }
            } label: {
                Label("Dismiss", systemImage: "xmark")
            }
        }
    }

    private func depositRow(_ deposit: Components.Schemas.IncomeAnalysisTransaction) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(deposit.merchant ?? deposit.name).lineLimit(1)
                Spacer()
                Text(deposit.amount.formattedExact)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.green)
            }
            Text(
                String(deposit.occurredAt.prefix(10))
                    + (deposit.accountName.map { " · \($0)" } ?? "")
            )
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.markIncome(deposit) }
            } label: {
                Label("Income", systemImage: "dollarsign.circle")
            }
            .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.markNotIncome(deposit) }
            } label: {
                Label("Not income", systemImage: "xmark")
            }
        }
    }

    static func frequencyText(_ frequency: Components.Schemas.RecurringFrequency) -> String {
        frequency.rawValue.capitalized
    }
}
