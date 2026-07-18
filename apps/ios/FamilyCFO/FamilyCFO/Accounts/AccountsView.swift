import SwiftUI

/// The Accounts tab (M99): every account grouped by kind so you can see where the
/// money is, and mark which accounts (or how much of them) make up the emergency
/// fund that safe-to-spend holds back.
struct AccountsView: View {
    @Environment(AppModel.self) private var model
    @State var viewModel: AccountsViewModel
    @State private var designating: Components.Schemas.Account?
    @State private var addingAccount = false

    var body: some View {
        NavigationStack {
            Group {
                if let errorMessage = viewModel.errorMessage, viewModel.accounts.isEmpty {
                    ContentUnavailableView {
                        Label("Can't load accounts", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else if viewModel.accounts.isEmpty && !viewModel.isLoading {
                    ContentUnavailableView(
                        "No accounts",
                        systemImage: "building.columns",
                        description: Text("Link a bank from the dashboard, or add a loan from the Debts tab."))
                } else {
                    List {
                        if let total = viewModel.emergencyFundTotal, total.amountMinor > 0 {
                            Section {
                                LabeledContent("Emergency fund", value: total.formatted)
                            } footer: {
                                Text("Total set aside across your accounts. Safe-to-spend holds this back.")
                            }
                        }
                        ForEach(viewModel.groups) { group in
                            Section(group.title) {
                                ForEach(group.accounts, id: \.id) { account in
                                    row(account)
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Accounts")
            .toolbar {
                // ADR 0034: adding accounts needs accounts.manage.
                if model.rolePolicy.canManageAccounts {
                    ToolbarItem(placement: .primaryAction) {
                        Button { addingAccount = true } label: {
                            Label("Add account", systemImage: "plus")
                        }
                    }
                }
            }
            .overlay { if viewModel.isLoading && viewModel.accounts.isEmpty { ProgressView() } }
            .refreshable {
                await viewModel.syncAndReload()
                model.syncStatus.markSynced()
            }
            .safeAreaInset(edge: .bottom) {
                SyncStatusFooter(status: model.syncStatus)
                    .padding(.vertical, 6)
            }
            .task { await viewModel.load() }
            .sheet(item: $designating) { account in
                AccountDetailSheet(account: account) { name, designation in
                    Task { await viewModel.save(account, name: name, designation: designation) }
                }
            }
            .sheet(isPresented: $addingAccount) {
                AddAccountSheet { name, type, balanceMinor in
                    Task { await viewModel.addAccount(name: name, type: type, balanceMinor: balanceMinor) }
                }
            }
        }
    }

    @ViewBuilder private func row(_ account: Components.Schemas.Account) -> some View {
        let designation = AccountsViewModel.designation(account)
        Button {
            designating = account
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(account.name).foregroundStyle(.primary).lineLimit(1)
                    if let institution = account.institution, !institution.isEmpty {
                        Text(institution).font(.caption).foregroundStyle(.secondary)
                    }
                    if designation != .none, let reserved = account.emergencyFundReserved {
                        Label("Emergency fund · \(reserved.formatted)", systemImage: "shield.fill")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(.green)
                    }
                }
                Spacer()
                Text(account.balance.formatted)
                    .font(.body.weight(.medium))
                Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .buttonStyle(.plain)
    }
}

/// Rename an account and (for asset accounts) designate how much is emergency fund.
private struct AccountDetailSheet: View {
    let account: Components.Schemas.Account
    let onSave: (String, EmergencyFundDesignation?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name: String
    @State private var mode: Mode
    @State private var amount: Double

    private enum Mode: Hashable { case none, whole, amount }

    private var canDesignate: Bool { AccountsViewModel.canHoldEmergencyFund(account) }

    init(
        account: Components.Schemas.Account,
        onSave: @escaping (String, EmergencyFundDesignation?) -> Void
    ) {
        self.account = account
        self.onSave = onSave
        _name = State(initialValue: account.name)
        switch AccountsViewModel.designation(account) {
        case .none: _mode = State(initialValue: .none); _amount = State(initialValue: 0)
        case .wholeBalance: _mode = State(initialValue: .whole); _amount = State(initialValue: 0)
        case .amount(let minor):
            _mode = State(initialValue: .amount)
            _amount = State(initialValue: Double(minor) / 100)
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Account name", text: $name)
                } header: {
                    Text("Name")
                } footer: {
                    if let institution = account.institution, !institution.isEmpty {
                        Text("\(institution) · balance \(account.balance.formatted). A name you set sticks through future syncs.")
                    } else {
                        Text("Give it a name you'll recognize.")
                    }
                }
                if canDesignate {
                    Section {
                        modeRow("Not emergency fund", .none)
                        modeRow("Whole balance", .whole)
                        modeRow("A set amount", .amount)
                        if mode == .amount {
                            HStack {
                                Text(account.balance.currency).foregroundStyle(.secondary)
                                TextField("Amount", value: $amount, format: .number.precision(.fractionLength(0...2)))
                                    .keyboardType(.decimalPad)
                                    .multilineTextAlignment(.trailing)
                            }
                        }
                    } header: {
                        Text("Emergency fund")
                    } footer: {
                        Text("How much of this account is untouchable savings that safe-to-spend holds back.")
                    }
                }
            }
            .navigationTitle("Edit account")
            .navigationBarTitleDisplayMode(.inline)
            .keyboardDoneButton()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        onSave(name, canDesignate ? designation : nil)
                        dismiss()
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    private func modeRow(_ title: String, _ value: Mode) -> some View {
        Button {
            mode = value
        } label: {
            HStack {
                Text(title).foregroundStyle(.primary)
                Spacer()
                if mode == value {
                    Image(systemName: "checkmark").foregroundStyle(.tint).fontWeight(.semibold)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var designation: EmergencyFundDesignation {
        switch mode {
        case .none: return .none
        case .whole: return .wholeBalance
        case .amount: return .amount(Int64((amount * 100).rounded()))
        }
    }
}

/// Add an account by hand — for holdings no bank feed reaches (e.g. an HSA).
private struct AddAccountSheet: View {
    let onSave: (String, Components.Schemas.AccountType, Int64) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var type: Components.Schemas.AccountType = .hsa
    @State private var amount: Double = 0

    var body: some View {
        NavigationStack {
            Form {
                Section("Name") {
                    TextField("e.g. HealthEquity HSA", text: $name)
                }
                Section("Type") {
                    Picker("Type", selection: $type) {
                        ForEach(manualAssetTypes, id: \.self) { t in
                            Text(Self.label(t)).tag(t)
                        }
                    }
                }
                Section {
                    HStack {
                        Text("$")
                        TextField("0.00", value: $amount, format: .number.precision(.fractionLength(0...2)))
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.trailing)
                    }
                } header: {
                    Text("Current balance")
                } footer: {
                    Text("You'll update this by editing the account whenever it changes — a manual account isn't synced.")
                }
            }
            .navigationTitle("Add account")
            .navigationBarTitleDisplayMode(.inline)
            .keyboardDoneButton()
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty else { return }
                        onSave(trimmed, type, Int64((amount * 100).rounded()))
                        dismiss()
                    }
                    .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    static func label(_ type: Components.Schemas.AccountType) -> String {
        switch type {
        case .checking: return "Checking"
        case .savings: return "Savings"
        case .hsa: return "HSA"
        case .brokerage: return "Brokerage / investment"
        case .retirement: return "Retirement"
        case ._529: return "529 college savings"
        case .realEstate: return "Real estate"
        case .otherAsset: return "Other asset"
        default: return "Account"
        }
    }
}
