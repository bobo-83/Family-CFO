import SwiftUI

/// "I already paid this" — pick the actual charge that paid a bill occurrence
/// when auto-matching missed it (a variable-amount bill, most often). Lists the
/// outflows near the row's due date, likeliest first; picking one links it, the
/// row flips to paid with that charge as its receipt, and safe-to-spend releases
/// the bill's claim. Tapping a row here is safe: a link is undoable (Unlink).
struct MarkPaidSheet: View {
    @Bindable var viewModel: BillsViewModel
    /// The timeline row being settled; its OWN `dueDate` names the occurrence.
    let item: Components.Schemas.PaymentTimelineItem

    @Environment(\.dismiss) private var dismiss
    /// nil while loading; [] when nothing was found near the due date.
    @State private var candidates: [Components.Schemas.Transaction]?
    @State private var loadFailed = false
    @State private var errorMessage: String?
    @State private var linking = false

    var body: some View {
        NavigationStack {
            List {
                if let errorMessage {
                    Label(errorMessage, systemImage: "exclamationmark.triangle")
                        .font(.caption).foregroundStyle(.red)
                }
                Section {
                    if let candidates {
                        if candidates.isEmpty && !loadFailed {
                            Text(
                                "No charges found near this due date — sync your accounts or add the transaction first."
                            )
                            .font(.callout)
                            .foregroundStyle(.secondary)
                        }
                        ForEach(candidates, id: \.id) { transaction in
                            candidateRow(transaction)
                        }
                    } else {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text("Finding charges…").foregroundStyle(.secondary)
                        }
                    }
                } header: {
                    Text("Charges near \(BillsView.shortDate(item.dueDate ?? ""))")
                } footer: {
                    Text(
                        "Pick the charge that paid this bill. Charges that look like “\(item.name)” are listed first."
                    )
                }
            }
            .navigationTitle(item.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .task { await loadCandidates() }
    }

    private func loadCandidates() async {
        guard let dueDate = item.dueDate else { return }
        let result = await viewModel.paymentCandidates(billID: item.id, dueDate: dueDate)
        loadFailed = result == nil
        errorMessage = result == nil ? viewModel.errorMessage : nil
        candidates = result ?? []
    }

    private func candidateRow(_ transaction: Components.Schemas.Transaction) -> some View {
        Button {
            link(transaction)
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(
                        verbatim: transaction.merchant ?? transaction.description
                            ?? String(localized: "Charge")
                    )
                    .lineLimit(1)
                    .foregroundStyle(.primary)
                    Text(
                        verbatim: BillsView.shortDate(transaction.occurredAt)
                            + (transaction.accountName.map { " · \($0)" } ?? "")
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Spacer()
                Text(verbatim: transaction.amount.formattedExact)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
            }
        }
        .disabled(linking)
    }

    private func link(_ transaction: Components.Schemas.Transaction) {
        guard let dueDate = item.dueDate, !linking else { return }
        linking = true
        Task {
            let linked = await viewModel.linkPayment(
                billID: item.id, transactionID: transaction.id, dueDate: dueDate)
            linking = false
            if linked {
                dismiss()
            } else {
                // The server's refusal (409 etc.) is user-appropriate — show it
                // here, verbatim, and keep the picker open to try another charge.
                errorMessage = viewModel.errorMessage
            }
        }
    }
}
