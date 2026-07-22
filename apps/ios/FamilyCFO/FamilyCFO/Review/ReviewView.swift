import SwiftUI

/// The Review tab (M97): exact-duplicate charges the bank reported more than once.
/// Each group is one set of identical charges — keep them all (a real repeat),
/// dispute one you didn't make, or delete an erroneous line. Disputing doesn't
/// contact the bank; it just tracks the charge here until you resolve it.
struct ReviewView: View {
    @Environment(AppModel.self) private var model
    let viewModel: ReviewViewModel
    @State private var pendingDelete: Components.Schemas.Transaction?
    @State private var recategorizing: Components.Schemas.Transaction?
    @State private var recategorizingGroup: ReviewViewModel.AmountGroup?

    // No NavigationStack of its own: pushed inside MoreView's stack — a
    // second stack here doubles the nav bars (user report 2026-07-22).
    var body: some View {
        Group {
            Group {
                if let errorMessage = viewModel.errorMessage, viewModel.nothingToReview {
                    ContentUnavailableView {
                        Label("Can't load review", systemImage: "wifi.exclamationmark")
                    } description: {
                        Text(errorMessage)
                    } actions: {
                        Button("Retry") { Task { await viewModel.load() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else if viewModel.nothingToReview && !viewModel.isLoading {
                    ContentUnavailableView(
                        "All clear",
                        systemImage: "checkmark.seal",
                        description: Text("Nothing to review — no possible duplicates, suspected income, transfers, or credits."))
                } else {
                    List {
                        if !viewModel.groups.isEmpty {
                            Section {
                                Text(
                                    "These charges came through more than once with the same account, date, amount, and merchant. Keep them if it's a real repeat, or dispute / delete a charge you didn't make."
                                )
                                .font(.footnote).foregroundStyle(.secondary)
                            }
                            ForEach(viewModel.groups) { group in
                                groupSection(group)
                            }
                        }
                        suspectedIncomeSection()
                        reviewList(
                            "Transfers", systemImage: "arrow.left.arrow.right",
                            count: viewModel.transfers.count, groups: viewModel.transferGroups,
                            highlightMoneyIn: false,
                            note: "Money moved between your own accounts (or card payments), grouped by amount — the two legs of a transfer sit together. Tap one to recategorize if it's really spending or income.")
                        reviewList(
                            "Credits & refunds", systemImage: "arrow.uturn.left",
                            count: viewModel.credits.count, groups: viewModel.creditGroups,
                            highlightMoneyIn: true,
                            note: "Money back — statement credits and refunds. Tap one to file it under the category it offsets (e.g. a Resy credit → Dining).")
                    }
                }
            }
            .navigationTitle("Review")
            .overlay {
                if viewModel.isLoading && viewModel.nothingToReview { ProgressView() }
            }
            .safeAreaInset(edge: .bottom) {
                if let undo = viewModel.lastUndo {
                    HStack {
                        Label("Moved \(undo.label)", systemImage: "checkmark.circle")
                            .font(.subheadline).lineLimit(1)
                        Spacer()
                        Button("Undo") { Task { await viewModel.undoRecategorize() } }
                            .font(.subheadline.weight(.semibold))
                    }
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
                    .padding(.horizontal).padding(.bottom, 6)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: undo.id) {
                        try? await Task.sleep(for: .seconds(6))
                        viewModel.clearUndo()
                    }
                }
            }
            .animation(.default, value: viewModel.lastUndo?.id)
            .safeAreaInset(edge: .bottom) {
                SyncStatusFooter(status: model.syncStatus)
                    .padding(.vertical, 6)
            }
            .refreshable {
                await viewModel.sync()
                model.syncStatus.markSynced()
            }
            .task { await viewModel.load() }
            .sheet(item: $recategorizing) { txn in
                CategoryPickerSheet(
                    title: txn.merchant ?? txn.description ?? "Transaction",
                    categories: viewModel.categories,
                    currentCategoryID: txn.categoryId,
                    onSelect: { newID in
                        guard let id = newID else { return }
                        Task { await viewModel.recategorize(txn, to: id) }
                    },
                    onDelete: { category in Task { await viewModel.deleteCategory(id: category.id) } })
            }
            .sheet(item: $recategorizingGroup) { group in
                CategoryPickerSheet(
                    title: "\(group.count) × \(group.sample.merchant ?? "transactions")",
                    categories: viewModel.categories,
                    currentCategoryID: group.sample.categoryId,
                    onSelect: { newID in
                        guard let id = newID else { return }
                        Task { await viewModel.recategorizeGroup(group, to: id) }
                    },
                    onDelete: { category in Task { await viewModel.deleteCategory(id: category.id) } })
            }
            .confirmationDialog(
                "Delete this charge?",
                isPresented: Binding(
                    get: { pendingDelete != nil },
                    set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible,
                presenting: pendingDelete
            ) { txn in
                Button("Delete Charge", role: .destructive) {
                    let target = txn
                    pendingDelete = nil
                    Task { await viewModel.delete(target) }
                }
                Button("Cancel", role: .cancel) { pendingDelete = nil }
            } message: { _ in
                Text("Removes this line from your data. Do this only for a charge that shouldn't exist — not to hide a real repeat.")
            }
        }
    }

    /// ADR 0049: sizeable inflows filed as a Transfer with no matching internal
    /// leg — likely paychecks/RSU the user should confirm as income. Each shows
    /// the value to confirm and two clear actions.
    @ViewBuilder private func suspectedIncomeSection() -> some View {
        if !viewModel.suspectedIncome.isEmpty {
            Section {
                ForEach(viewModel.suspectedIncome) { txn in
                    suspectedIncomeRow(txn)
                }
            } header: {
                Label("Suspected income (\(viewModel.suspectedIncome.count))", systemImage: "dollarsign.circle")
                    .font(.subheadline.weight(.semibold))
                    .textCase(nil)
            } footer: {
                Text("These landed in one of your accounts as a large \"Transfer\" with no matching move out of another account — usually a paycheck or deposit filed in the wrong place. Confirm the amount to count it as income, or keep it as a transfer.")
                    .font(.caption)
            }
        }
    }

    @ViewBuilder private func suspectedIncomeRow(
        _ txn: Components.Schemas.Transaction
    ) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(txn.merchant ?? txn.description ?? "Deposit").lineLimit(1)
                        .font(.subheadline.weight(.medium))
                    if let source = sourceLine(txn) {
                        Text(source).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Text(String(txn.occurredAt.prefix(10)))
                        .font(.caption2).foregroundStyle(.secondary)
                    badge("Suspected income", .green)
                }
                Spacer()
                Text(txn.amount.formattedExact)
                    .font(.headline)
                    .foregroundStyle(Color.green)
            }
            HStack(spacing: 10) {
                Button {
                    Task { await viewModel.confirmAsIncome(txn) }
                } label: {
                    Label("Confirm as income", systemImage: "checkmark.circle.fill")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                Button {
                    Task { await viewModel.keepAsTransfer(txn) }
                } label: {
                    Text("Keep as transfer")
                        .font(.subheadline)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 4)
    }

    @ViewBuilder private func groupSection(_ group: ReviewViewModel.ReviewGroup) -> some View {
        Section {
            ForEach(group.transactions, id: \.id) { txn in
                chargeRow(txn)
            }
            if !group.hasDisputed {
                Button {
                    Task { await viewModel.keepAll(group) }
                } label: {
                    Label("Keep all — this is a real repeat", systemImage: "checkmark.circle")
                }
            }
        } header: {
            VStack(alignment: .leading, spacing: 2) {
                Text(group.sample.merchant ?? group.sample.description ?? "Transaction")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .textCase(nil)
                if let source = sourceLine(group.sample) {
                    Text(source)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textCase(nil)
                }
            }
        } footer: {
            Text(
                "\(group.count) identical charges of \(group.sample.amount.formattedExact) on \(String(group.sample.occurredAt.prefix(10)))"
                    + (group.hasDisputed
                        ? ". Disputing tracks it here — contact your bank to actually dispute it."
                        : ".")
            )
        }
    }

    /// A collapsible section (Transfers, Credits). Same-amount transactions are
    /// grouped into a nested expandable row to save vertical space; a lone one is
    /// shown directly. Tapping a transaction recategorizes it.
    @ViewBuilder private func reviewList(
        _ title: String, systemImage: String, count: Int,
        groups: [ReviewViewModel.AmountGroup], highlightMoneyIn: Bool, note: String
    ) -> some View {
        if count > 0 {
            Section {
                DisclosureGroup {
                    ForEach(groups) { group in
                        amountGroupRow(group, highlightMoneyIn: highlightMoneyIn)
                    }
                } label: {
                    Label("\(title) (\(count))", systemImage: systemImage)
                        .font(.subheadline.weight(.semibold))
                }
            } footer: {
                Text(note).font(.caption)
            }
        }
    }

    @ViewBuilder private func amountGroupRow(
        _ group: ReviewViewModel.AmountGroup, highlightMoneyIn: Bool
    ) -> some View {
        if group.count == 1 {
            Button { recategorizing = group.sample } label: {
                reviewRow(group.sample, highlightMoneyIn: highlightMoneyIn)
            }
            .buttonStyle(.plain)
        } else {
            DisclosureGroup {
                Button { recategorizingGroup = group } label: {
                    Label("Categorize all \(group.count)", systemImage: "tag")
                        .font(.subheadline.weight(.medium))
                }
                ForEach(group.transactions, id: \.id) { txn in
                    Button { recategorizing = txn } label: {
                        reviewRow(txn, highlightMoneyIn: highlightMoneyIn)
                    }
                    .buttonStyle(.plain)
                }
            } label: {
                amountGroupLabel(group)
            }
        }
    }

    private func amountGroupLabel(_ group: ReviewViewModel.AmountGroup) -> some View {
        let money = Components.Schemas.Money(
            amountMinor: group.absAmount, currency: group.sample.amount.currency)
        return HStack {
            Text(group.sample.merchant ?? group.sample.description ?? "Transfer").lineLimit(1)
            Text("· \(group.count)").foregroundStyle(.secondary)
            Spacer()
            Text(money.formattedExact).font(.subheadline.weight(.medium))
        }
    }

    @ViewBuilder private func reviewRow(
        _ txn: Components.Schemas.Transaction, highlightMoneyIn: Bool
    ) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(txn.merchant ?? txn.description ?? "Transaction").lineLimit(1)
                    .foregroundStyle(.primary)
                if let note = txn.note, !note.isEmpty {
                    Label(note, systemImage: "note.text")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                if let source = sourceLine(txn) {
                    Text(source).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                if let category = txn.category {
                    Text(category).font(.caption2).foregroundStyle(.tertiary)
                }
                Text(String(txn.occurredAt.prefix(10))).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
            Text(txn.amount.formattedExact)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(highlightMoneyIn && txn.amount.amountMinor > 0 ? Color.green : .primary)
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder private func chargeRow(_ txn: Components.Schemas.Transaction) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(txn.amount.formattedExact)
                        .font(.body.weight(.medium))
                    Text(String(txn.occurredAt.prefix(10)))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if let detail = txn.rawDetail {
                    Text(detail).font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                }
                HStack(spacing: 8) {
                    stateBadge(txn.duplicateState)
                    if let ref = txn.shortReference {
                        Text("Bank ref …\(ref)")
                            .font(.caption2.monospaced())
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            Spacer()
        }
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button(role: .destructive) { pendingDelete = txn } label: {
                Label("Delete", systemImage: "trash")
            }
            if txn.duplicateState == .disputed {
                Button { Task { await viewModel.resolveDispute(txn) } } label: {
                    Label("Resolve", systemImage: "checkmark")
                }
                .tint(.green)
            } else {
                Button { Task { await viewModel.dispute(txn) } } label: {
                    Label("Dispute", systemImage: "exclamationmark.bubble")
                }
                .tint(.orange)
            }
        }
    }

    /// "American Express · Platinum Card® (3006)" — where to go look this up.
    private func sourceLine(_ txn: Components.Schemas.Transaction) -> String? {
        switch (txn.institution, txn.accountName) {
        case let (institution?, account?): return "\(institution) · \(account)"
        case let (institution?, nil): return institution
        case let (nil, account?): return account
        default: return nil
        }
    }

    @ViewBuilder private func stateBadge(
        _ state: Components.Schemas.Transaction.DuplicateStatePayload?
    ) -> some View {
        switch state {
        case .disputed:
            badge("Disputed", .red)
        default:
            badge("Possible duplicate", .orange)
        }
    }

    private func badge(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15))
            .foregroundStyle(color)
            .clipShape(Capsule())
    }
}
