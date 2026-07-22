import SwiftUI

/// The Income tab: the analyzed income picture (detected sources, rollup, tax
/// estimate), the household's income earners, and the W-2 scan on-ramp — its
/// own first-class page, like Bills and Debts, instead of a camera shortcut
/// buried on Overview.
/// `IncomeAnalysisTransaction` carries a stable id, so it can drive `.sheet(item:)`.
extension Components.Schemas.IncomeAnalysisTransaction: Identifiable {
    public var id: String { transactionId }
}

struct IncomeView: View {
    @State var viewModel: IncomeViewModel
    @State private var recategorizing: Components.Schemas.IncomeAnalysisTransaction?

    // No NavigationStack of its own: pushed inside MoreView's stack — a
    // second stack here doubles the nav bars (user report 2026-07-22).
    var body: some View {
        Group {
            Group {
                if let errorMessage = viewModel.errorMessage, viewModel.analysis == nil {
                    ContentUnavailableView {
                        Label("Can't load income", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else if let analysis = viewModel.analysis {
                    content(analysis)
                } else {
                    ProgressView()
                }
            }
            .navigationTitle("Income")
            .task { await viewModel.load() }
            .refreshable { await viewModel.load() }
            .sheet(item: $recategorizing) { txn in
                CategoryPickerSheet(
                    title: txn.merchant ?? txn.name,
                    categories: viewModel.categories,
                    currentCategoryID: nil,
                    onSelect: { newID in
                        guard let id = newID else { return }
                        Task { await viewModel.recategorize(txn, to: id) }
                    })
            }
        }
    }

    private func content(_ analysis: Components.Schemas.IncomeAnalysisResponse) -> some View {
        List {
            Section("This year") {
                LabeledContent("Annual income", value: analysis.rollup.annualIncome.formatted)
                LabeledContent("Monthly average", value: analysis.rollup.monthlyAverage.formatted)
                LabeledContent("Estimated gross", value: analysis.tax.grossIncome.formatted)
            }

            if let warning = analysis.coverageWarning {
                Section {
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
            }

            if !analysis.sources.isEmpty {
                Section("Income sources") {
                    ForEach(analysis.sources, id: \.sourceKey) { source in
                        DisclosureGroup {
                            ForEach(source.transactions, id: \.transactionId) { txn in
                                incomeDepositRow(txn)
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(source.name)
                                Text(
                                    "\(source.totalAmount.formatted) · \(source.transactions.count) deposit\(source.transactions.count == 1 ? "" : "s") · \(source.frequency)"
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }

            Section {
                ForEach(viewModel.earners, id: \.id) { earner in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(earner.label)
                        Text("Base \(earner.baseSalary.formatted)/yr")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .swipeActions {
                        Button(role: .destructive) {
                            Task { await viewModel.deleteEarner(earner) }
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                    }
                }
                NavigationLink {
                    W2ScanView(viewModel: W2ScanViewModel(api: viewModel.api))
                } label: {
                    Label("Add earner (scan a W-2)", systemImage: "doc.text.viewfinder")
                }
            } header: {
                Text("Earners")
            } footer: {
                Text("Scan a W-2 to prefill an earner — or type the figures in by hand on the same screen. Swipe an earner to delete it.")
            }

            if let errorMessage = viewModel.errorMessage {
                Label(errorMessage, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
    }

    /// One income deposit: the amount and date, expandable to its evidence —
    /// payer, bank, account, and the bank memo (which often names an RSU/ESPP
    /// sale in a brokerage). ADR 0054.
    @ViewBuilder private func incomeDepositRow(
        _ txn: Components.Schemas.IncomeAnalysisTransaction
    ) -> some View {
        DisclosureGroup {
            VStack(alignment: .leading, spacing: 4) {
                detailLine("Date", String(txn.occurredAt.prefix(10)))
                detailLine("From / payer", txn.merchant ?? txn.name)
                if let bank = txn.institution, !bank.isEmpty { detailLine("Bank", bank) }
                if let account = txn.accountName, !account.isEmpty {
                    detailLine("Account", account)
                }
                if let memo = txn.description, !memo.isEmpty { detailLine("Bank memo", memo) }
                detailLine("Amount", txn.amount.formatted)
                Button {
                    recategorizing = txn
                } label: {
                    Label("Recategorize — not income?", systemImage: "tag")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
                .padding(.top, 2)
            }
            .padding(.vertical, 2)
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(txn.name).font(.subheadline).lineLimit(1)
                    Text(String(txn.occurredAt.prefix(10)))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text(txn.amount.formatted)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(Color.green)
            }
        }
    }

    private func detailLine(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 96, alignment: .leading)
            Text(value)
                .font(.caption)
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }
}
