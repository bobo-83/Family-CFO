import SwiftUI

/// #25: the statement's charges, checked against what the bank feed delivered.
///
/// The unmatched lines lead and carry the weight — they are the finding. The
/// matched ones are kept, quietly, so the check is auditable rather than a
/// number to be taken on trust.
struct StatementReconciliationView: View {
    @State var viewModel: StatementReconciliationViewModel

    private var unmatched: [Components.Schemas.StatementLine] {
        viewModel.lines.filter { StatementReconciliationViewModel.state(of: $0) != .matched }
    }

    private var matched: [Components.Schemas.StatementLine] {
        viewModel.lines.filter { StatementReconciliationViewModel.state(of: $0) == .matched }
    }

    var body: some View {
        List {
            if let errorMessage = viewModel.errorMessage {
                Section {
                    Label {
                        Text(verbatim: errorMessage)
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                    }
                    .font(.caption)
                    .foregroundStyle(.orange)
                }
            }
            if viewModel.coverage.total > 0 {
                coverageSection
            }
            if !unmatched.isEmpty {
                Section {
                    ForEach(unmatched) { line in
                        lineRow(line)
                    }
                } header: {
                    Text("Charges to look at")
                } footer: {
                    Text("A charge your statement lists that no synced transaction explains is missing from your ledger — the feed never delivered it. Sync again, and if it still doesn't appear, add it yourself.")
                }
            }
            if !matched.isEmpty {
                Section {
                    ForEach(matched) { line in
                        lineRow(line)
                    }
                } header: {
                    Text("Already accounted for")
                }
            }
            // With no lines stored, EVERY transaction in the cycle is "not on
            // the statement" — a list that says nothing. The empty state
            // explains the real problem instead.
            if !viewModel.unaccounted.isEmpty && !viewModel.hasNoStoredLines {
                Section {
                    ForEach(viewModel.unaccounted, id: \.transactionId) { extra in
                        unaccountedRow(extra)
                    }
                } header: {
                    Text("Synced, but not on this statement")
                } footer: {
                    Text("These almost always posted after the cycle closed, so they belong to the next statement. Only look twice if one of them duplicates a charge above.")
                }
            }
        }
        .navigationTitle("Line items")
        .navigationBarTitleDisplayMode(.inline)
        .overlay {
            if viewModel.isLoading && viewModel.reconciliation == nil {
                ProgressView()
            } else if viewModel.hasNoStoredLines {
                ContentUnavailableView(
                    "No line items yet",
                    systemImage: "list.bullet.rectangle",
                    description: Text("Nothing has been read from this statement's transaction table yet. Scan the statement from the previous screen and store what the reader finds."))
            }
        }
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }

    private var coverageSection: some View {
        Section {
            Text(verbatim: StatementReconciliationViewModel.coverageSummary(viewModel.coverage))
                .font(.subheadline.weight(.medium))
        } header: {
            Text(verbatim: viewModel.subtitle)
        } footer: {
            Text("Checked fresh every time you open this — a sync that fills a gap closes it here without re-reading the statement.")
        }
    }

    @ViewBuilder private func lineRow(_ line: Components.Schemas.StatementLine) -> some View {
        let state = StatementReconciliationViewModel.state(of: line)
        let isMatched = state == .matched
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: Self.icon(for: state))
                .font(isMatched ? .caption : .body)
                .foregroundStyle(Self.tint(for: state))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: line.description)
                    .font(isMatched ? .subheadline : .body.weight(.semibold))
                    .foregroundStyle(isMatched ? Color.secondary : Color.primary)
                    .lineLimit(2)
                HStack(spacing: 4) {
                    Text(verbatim: BillsView.shortDate(line.occurredOn))
                        .foregroundStyle(.secondary)
                    Text(verbatim: "·").foregroundStyle(.secondary)
                    Text(verbatim: StatementReconciliationViewModel.label(for: state))
                        .foregroundStyle(isMatched ? Color.secondary : Self.tint(for: state))
                }
                .font(.caption)
            }
            Spacer(minLength: 8)
            Text(verbatim: line.amount.formattedExact)
                .font(isMatched ? .subheadline : .body.weight(.medium))
                .foregroundStyle(isMatched ? Color.secondary : Color.primary)
                .monospacedDigit()
        }
    }

    private func unaccountedRow(
        _ extra: Components.Schemas.UnaccountedTransaction
    ) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(verbatim: extra.merchant).font(.subheadline).lineLimit(2)
                Text(verbatim: BillsView.shortDate(extra.occurredAt))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Text(verbatim: extra.amount.formattedExact)
                .font(.subheadline)
                .monospacedDigit()
        }
    }

    private static func icon(for state: StatementReconciliationViewModel.MatchState) -> String {
        switch state {
        case .missing: return "exclamationmark.circle.fill"
        case .amountDiffers: return "questionmark.circle.fill"
        case .matched: return "checkmark.circle.fill"
        }
    }

    private static func tint(for state: StatementReconciliationViewModel.MatchState) -> Color {
        switch state {
        case .missing: return .red
        case .amountDiffers: return .orange
        case .matched: return .secondary
        }
    }
}
