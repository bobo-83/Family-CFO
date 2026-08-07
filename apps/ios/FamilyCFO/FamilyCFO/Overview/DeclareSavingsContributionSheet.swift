import SwiftUI

/// #203: say what you're saving when the app can't see it. Detection needs both
/// legs of the transfer in the ledger; a destination that never syncs (a 529, a
/// workplace plan) leaves no arrival to match, so two detector iterations found
/// nothing on real households. A declaration is the household's own word and
/// outranks any detection on the same route.
struct DeclareSavingsContributionSheet: View {
    @Bindable var viewModel: OverviewViewModel
    let accountsAPI: AccountsAPI
    /// The household's base currency, so the amount field and the posted Money
    /// agree with every other figure on the Overview.
    let currency: String

    @Environment(\.dismiss) private var dismiss
    /// nil while loading; [] when the fetch failed or there is nothing to pick.
    @State private var accounts: [Components.Schemas.Account]?
    @State private var sourceID: String?
    @State private var destinationID: String?
    @State private var amount: Double?
    @State private var frequency: Components.Schemas.RecurringFrequency = .monthly
    @State private var errorMessage: String?
    @State private var saving = false

    /// Saving runs asset → asset; a card or a loan is never either leg.
    private var eligible: [Components.Schemas.Account] {
        (accounts ?? []).filter { manualAssetTypes.contains($0._type) }
    }

    private var canSave: Bool {
        guard let sourceID, let destinationID else { return false }
        return sourceID != destinationID && (amount ?? 0) > 0 && !saving
    }

    var body: some View {
        NavigationStack {
            Form {
                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.red)
                }
                if accounts == nil {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Loading accounts…").foregroundStyle(.secondary)
                    }
                } else if eligible.count < 2 {
                    Text(
                        "You need both accounts on file first. Add the destination by hand on "
                            + "the Accounts tab — an account that never syncs (a 529, a workplace "
                            + "plan) can still be tracked."
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                } else {
                    Section {
                        Picker("From", selection: $sourceID) {
                            ForEach(eligible) { account in
                                Text(account.name).tag(Optional(account.id))
                            }
                        }
                        Picker("To", selection: $destinationID) {
                            ForEach(eligible) { account in
                                Text(account.name).tag(Optional(account.id))
                            }
                        }
                    } header: {
                        Text("The transfer")
                    } footer: {
                        Text(
                            "Where the money leaves from, and the savings vehicle it lands in. "
                                + "Neither side has to sync."
                        )
                    }
                    Section {
                        TextField("Amount", value: $amount, format: .currency(code: currency))
                            .keyboardType(.decimalPad)
                        Picker("How often", selection: $frequency) {
                            ForEach(Components.Schemas.RecurringFrequency.allCases, id: \.self) { f in
                                Text(OverviewView.cadenceWord(f)).tag(f)
                            }
                        }
                    } header: {
                        Text("How much")
                    } footer: {
                        Text(
                            "Counted at its monthly run-rate in what you're saving, and reserved "
                                + "by \"Left to spend this month\"."
                        )
                    }
                }
            }
            .navigationTitle("Declare savings")
            .keyboardDoneButton()
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Add") { save() }
                        .disabled(!canSave)
                }
            }
        }
        .task { await loadAccounts() }
    }

    private func loadAccounts() async {
        guard accounts == nil else { return }
        do {
            accounts = try await accountsAPI.accounts()
            preselect()
        } catch {
            accounts = []
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Cash out, savings vehicle in — the shape of every contribution this sheet
    /// exists for. Only a starting point; both pickers stay open.
    private func preselect() {
        let vehicles: Set<Components.Schemas.AccountType> = [._529, .retirement, .hsa, .brokerage]
        sourceID =
            eligible.first { $0._type == .checking }?.id
            ?? eligible.first { $0._type == .savings }?.id
            ?? eligible.first?.id
        destinationID =
            eligible.first { vehicles.contains($0._type) && $0.id != sourceID }?.id
            ?? eligible.first { $0.id != sourceID }?.id
    }

    private func save() {
        guard let sourceID, let destinationID, let amount, amount > 0, !saving else { return }
        saving = true
        Task {
            let posted = await viewModel.declareContribution(
                .init(
                    sourceAccountId: sourceID,
                    destinationAccountId: destinationID,
                    amount: .init(
                        amountMinor: Int64((amount * 100).rounded()), currency: currency),
                    frequency: frequency))
            saving = false
            if posted {
                dismiss()
            } else {
                // Keep what was typed on screen: the server's refusal (a sealed
                // household, a stale account id) is fixable without retyping.
                errorMessage = viewModel.errorMessage
            }
        }
    }
}
