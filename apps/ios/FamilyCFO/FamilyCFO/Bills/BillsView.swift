import SwiftUI

/// The Bills tab (M90/M95): recurring-bill suggestions to confirm, the current
/// bills with add/delete, a re-sync of linked accounts, and — when the income
/// analysis flags any — deposits to mark as income. Adults-only; every action
/// changes household money data, the same gate the server enforces.
struct BillsView: View {
    @Bindable var viewModel: BillsViewModel
    @State private var addingBill = false

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.errorMessage != nil && viewModel.bills.isEmpty
                    && viewModel.billSuggestions.isEmpty
                {
                    ContentUnavailableView {
                        Label("Can't reach your CFO", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(viewModel.errorMessage ?? "")
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else {
                    list
                }
            }
            .navigationTitle("Bills")
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button { addingBill = true } label: {
                        Label("Add bill", systemImage: "plus")
                    }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        Task { await viewModel.sync() }
                    } label: {
                        if viewModel.isSyncing {
                            ProgressView()
                        } else {
                            Label("Sync", systemImage: "arrow.clockwise")
                        }
                    }
                    .disabled(viewModel.isSyncing)
                }
            }
            .sheet(isPresented: $addingBill) {
                AddBillView(viewModel: viewModel)
            }
        }
        .task { await viewModel.load() }
    }

    private var list: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.red)
            }
            if let syncResult = viewModel.syncResult {
                Label(syncResult, systemImage: "checkmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }

            if !viewModel.billSuggestions.isEmpty {
                Section {
                    ForEach(viewModel.billSuggestions, id: \.merchantKey) { suggestion in
                        suggestionRow(suggestion)
                    }
                } header: {
                    Text("Suggested bills")
                } footer: {
                    Text("Recurring charges found in your spending. Swipe to add or dismiss.")
                }
            }

            Section("Your bills") {
                if viewModel.bills.isEmpty {
                    Text(viewModel.isLoading ? "Loading…" : "No bills yet. Add one with +, or confirm a suggestion.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.bills, id: \.id) { bill in
                    billRow(bill)
                        .swipeActions(edge: .trailing) {
                            Button(role: .destructive) {
                                Task { await viewModel.deleteBill(bill) }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                }
            }

            if !viewModel.deposits.isEmpty {
                Section {
                    ForEach(viewModel.deposits, id: \.transactionId) { deposit in
                        depositRow(deposit)
                    }
                } header: {
                    Text("Deposits — income?")
                } footer: {
                    Text("Money that came in that we couldn't classify. Swipe to mark it.")
                }
            }
        }
        .refreshable { await viewModel.load() }
    }

    private func suggestionRow(_ suggestion: Components.Schemas.BillSuggestion) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(suggestion.name).lineLimit(1)
                Spacer()
                Text(suggestion.amount.formattedExact).font(.subheadline.weight(.medium))
            }
            Text(
                "\(Self.frequencyText(suggestion.frequency)) · seen \(suggestion.occurrences)× · "
                    + "next \(String(suggestion.nextDueDate.prefix(10)))"
            )
            .font(.caption).foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.confirmBill(suggestion) }
            } label: { Label("Add bill", systemImage: "checkmark") }
                .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.dismissBill(suggestion) }
            } label: { Label("Dismiss", systemImage: "xmark") }
        }
    }

    private func billRow(_ bill: Components.Schemas.Bill) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(bill.name).lineLimit(1)
                Text(
                    Self.frequencyText(bill.frequency)
                        + (bill.nextDueDate.map { " · next \(String($0.prefix(10)))" } ?? "")
                )
                .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(bill.amount.formattedExact).font(.subheadline.weight(.medium))
        }
    }

    private func depositRow(_ deposit: Components.Schemas.IncomeAnalysisTransaction) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(deposit.merchant ?? deposit.name).lineLimit(1)
                Spacer()
                Text(deposit.amount.formattedExact)
                    .font(.subheadline.weight(.medium)).foregroundStyle(.green)
            }
            Text(
                String(deposit.occurredAt.prefix(10))
                    + (deposit.accountName.map { " · \($0)" } ?? "")
            )
            .font(.caption).foregroundStyle(.secondary)
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                Task { await viewModel.markIncome(deposit) }
            } label: { Label("Income", systemImage: "dollarsign.circle") }
                .tint(.green)
            Button(role: .destructive) {
                Task { await viewModel.markNotIncome(deposit) }
            } label: { Label("Not income", systemImage: "xmark") }
        }
    }

    static func frequencyText(_ frequency: Components.Schemas.RecurringFrequency) -> String {
        frequency.rawValue.capitalized
    }
}

/// Add a bill by hand (M95): name, amount, how often, and when it's next due.
struct AddBillView: View {
    @Environment(\.dismiss) private var dismiss
    let viewModel: BillsViewModel

    @State private var name = ""
    @State private var amount: Decimal?
    @State private var frequency: Components.Schemas.RecurringFrequency = .monthly
    @State private var nextDue = Date()

    private var currency: String { "USD" }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name (e.g. Rent)", text: $name)
                    TextField("Amount", value: $amount, format: .currency(code: currency))
                        .keyboardType(.decimalPad)
                    Picker("Repeats", selection: $frequency) {
                        ForEach(Self.frequencies, id: \.self) { f in
                            Text(BillsView.frequencyText(f)).tag(f)
                        }
                    }
                    DatePicker("Next due", selection: $nextDue, displayedComponents: .date)
                }
            }
            .navigationTitle("Add bill")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { add() }
                        .disabled(
                            name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                                || (amount ?? 0) <= 0)
                }
            }
        }
    }

    private func add() {
        var cents = (amount ?? 0) * 100
        var rounded = Decimal()
        NSDecimalRound(&rounded, &cents, 0, .plain)
        let minor = Int64(truncating: rounded as NSDecimalNumber)
        let due = Self.isoDate(nextDue)
        dismiss()
        Task {
            await viewModel.addBill(
                name: name, amountMinor: minor, currency: currency,
                frequency: frequency, nextDueDate: due)
        }
    }

    static let frequencies: [Components.Schemas.RecurringFrequency] =
        [.weekly, .biweekly, .semimonthly, .monthly, .quarterly, .annual]

    static func isoDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.calendar = Calendar(identifier: .gregorian)
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}
