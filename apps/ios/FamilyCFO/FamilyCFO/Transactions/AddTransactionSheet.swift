import SwiftUI

/// #29: record a transaction by hand — the cash purchase no feed carries, or
/// the statement charge the feed never delivered (#25).
///
/// The amount is entered as a plain positive number with an explicit
/// expense/income choice above it. The ledger stores spending as a negative,
/// but asking a phone user to type a minus sign is how a refund gets recorded
/// as a purchase; the choice makes the direction unmissable instead.
struct AddTransactionSheet: View {
    @Bindable var viewModel: AddTransactionViewModel
    /// Called after a transaction is actually stored, so the screen behind can
    /// re-read. Never called for a cancel.
    let onSaved: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                if viewModel.isPrefilled {
                    Section {
                        Label(
                            "Filled in from a charge on your statement that no synced transaction explained. Check every value — nothing is recorded until you tap Save.",
                            systemImage: "info.circle"
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }
                if let errorMessage = viewModel.errorMessage {
                    Section {
                        Label {
                            Text(verbatim: errorMessage)
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill")
                        }
                        .font(.caption)
                        .foregroundStyle(.red)
                    }
                }
                Section {
                    if viewModel.accounts.isEmpty {
                        if viewModel.isLoading {
                            HStack(spacing: 8) {
                                ProgressView()
                                Text("Loading accounts…").foregroundStyle(.secondary)
                            }
                        } else {
                            Text("No account to record this against yet — link a bank, or add one by hand on the Accounts tab.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    } else {
                        Picker("Account", selection: $viewModel.accountID) {
                            ForEach(viewModel.accounts) { account in
                                Text(verbatim: account.name).tag(Optional(account.id))
                            }
                        }
                    }
                } header: {
                    Text("Account")
                } footer: {
                    Text("Where the money moved. A cash purchase belongs to the account you'd have drawn the cash from.")
                }
                Section {
                    Picker("Direction", selection: $viewModel.direction) {
                        ForEach(AddTransactionViewModel.Direction.allCases, id: \.self) { option in
                            Text(option.label).tag(option)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    HStack {
                        Text(verbatim: viewModel.currency).foregroundStyle(.secondary)
                        TextField(
                            "0.00", value: $viewModel.amount,
                            format: .number.precision(.fractionLength(0...2))
                        )
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                    }
                    DatePicker("Date", selection: $viewModel.date, displayedComponents: .date)
                } header: {
                    Text("Amount")
                } footer: {
                    Text("Enter it as a positive number. An expense is stored as money out and a refund or payment received as money in — you never type a minus sign.")
                }
                Section {
                    TextField("e.g. Corner Market", text: $viewModel.merchant)
                        .textInputAutocapitalization(.words)
                } header: {
                    Text("Merchant")
                } footer: {
                    Text("Who the money went to (or came from). Optional, but it's what you'll recognize this row by later.")
                }
                Section {
                    Picker("Category", selection: $viewModel.categoryID) {
                        Text("Uncategorized").tag(String?.none)
                        ForEach(viewModel.categories, id: \.id) { category in
                            Text(verbatim: category.name).tag(Optional(category.id))
                        }
                    }
                    TextField("Note", text: $viewModel.note, axis: .vertical)
                        .lineLimit(1...3)
                } header: {
                    Text("Details")
                } footer: {
                    Text("Both optional — you can file it later from the Categories screen.")
                }
            }
            .navigationTitle("Add transaction")
            .navigationBarTitleDisplayMode(.inline)
            .keyboardDoneButton()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(!viewModel.canSave)
                }
            }
            .task { await viewModel.load() }
        }
        .presentationDetents([.medium, .large])
    }

    private func save() {
        Task {
            guard await viewModel.save() else { return }
            onSaved()
            dismiss()
        }
    }
}
