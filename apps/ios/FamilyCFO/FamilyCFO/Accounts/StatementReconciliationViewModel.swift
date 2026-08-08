import Foundation
import Observation

/// #25: does the synced ledger actually account for this statement?
///
/// The recorded balance says what the card asks for. Its LINE ITEMS answer the
/// question that matters more for a picture built on a bank feed: **is what I'm
/// looking at complete?** A charge the statement prints that no synced
/// transaction explains is a hole in the ledger — so unmatched lines lead here
/// and matched ones recede.
///
/// Read-only on the ledger. Nothing on this screen creates a transaction; the
/// most an unmatched line can do is hand its values to the add-transaction flow
/// for a person to confirm.
@MainActor
@Observable
final class StatementReconciliationViewModel {
    private let api: AccountsAPI
    let statement: Components.Schemas.CardStatement

    private(set) var reconciliation: Components.Schemas.StatementReconciliation?
    private(set) var isLoading = false
    var errorMessage: String?

    init(api: AccountsAPI, statement: Components.Schemas.CardStatement) {
        self.api = api
        self.statement = statement
    }

    /// The server re-matches on every read, so pulling to refresh after a sync
    /// is the whole fix for a gap the feed has since filled.
    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            reconciliation = try await api.statementReconciliation(statementID: statement.id)
            errorMessage = nil
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// Unmatched first — see `ordered(_:)`.
    var lines: [Components.Schemas.StatementLine] {
        Self.ordered(reconciliation?.lines ?? [])
    }

    var unaccounted: [Components.Schemas.UnaccountedTransaction] {
        reconciliation?.unaccounted ?? []
    }

    var coverage: Coverage { Self.coverage(reconciliation) }

    /// True once a read has landed and it found no stored lines — the statement
    /// balance was recorded but its charges never were.
    var hasNoStoredLines: Bool { reconciliation != nil && coverage.total == 0 }

    // MARK: - What a line's match means

    /// `match_kind` and `matched_transaction_id` read together. The wire values
    /// are stable identifiers, never user-facing text — every row goes through
    /// here so the same state reads the same way in every language.
    enum MatchState: Int, Comparable {
        /// On the statement, never delivered by the feed. The point of #25.
        case missing = 0
        /// A transaction looks like this charge but the amounts disagree.
        case amountDiffers = 1
        case matched = 2

        static func < (lhs: Self, rhs: Self) -> Bool { lhs.rawValue < rhs.rawValue }
    }

    static func state(of line: Components.Schemas.StatementLine) -> MatchState {
        guard line.matchedTransactionId != nil else { return .missing }
        return line.matchKind == "amount_differs" ? .amountDiffers : .matched
    }

    static func label(for state: MatchState) -> String {
        switch state {
        case .missing:
            return String(localized: "Not in your ledger")
        case .amountDiffers:
            return String(localized: "Amount differs")
        case .matched:
            return String(localized: "Matched")
        }
    }

    /// Unmatched first, then the near-misses, then the matched ones — each group
    /// oldest first. A gap in the feed is the reason to open this screen, so it
    /// must never be buried under a hundred rows that are fine.
    static func ordered(
        _ lines: [Components.Schemas.StatementLine]
    ) -> [Components.Schemas.StatementLine] {
        lines.sorted { first, second in
            let left = state(of: first)
            let right = state(of: second)
            if left != right { return left < right }
            return first.occurredOn < second.occurredOn
        }
    }

    // MARK: - Coverage

    /// The counts the summary line is built from.
    struct Coverage: Equatable {
        /// Lines a transaction explains — including the near-misses counted
        /// again in `differs`, which is how the server counts them.
        var matched = 0
        /// Lines the bank feed never delivered.
        var missing = 0
        var differs = 0
        /// Synced transactions this statement doesn't list.
        var postedAfterClose = 0

        /// Every stored line either matched or didn't, so the pair is the whole
        /// statement — the server needn't send a separate total.
        var total: Int { matched + missing }
    }

    static func coverage(
        _ reconciliation: Components.Schemas.StatementReconciliation?
    ) -> Coverage {
        Coverage(
            matched: reconciliation?.matchedCount ?? 0,
            missing: reconciliation?.missingFromSyncCount ?? 0,
            differs: reconciliation?.amountDiffersCount ?? 0,
            postedAfterClose: reconciliation?.notOnStatementCount ?? 0)
    }

    /// "2 of 3 charges matched · 1 not synced · 1 amount differs · 1 posted
    /// after close". A count that's zero is left out rather than printed as a
    /// zero — the summary should read as a finding, not a form.
    static func coverageSummary(_ coverage: Coverage) -> String {
        var parts = [
            String(localized: "\(coverage.matched) of \(coverage.total) charges matched")
        ]
        if coverage.missing > 0 {
            parts.append(String(localized: "\(coverage.missing) not synced"))
        }
        if coverage.differs > 0 {
            parts.append(String(localized: "\(coverage.differs) amount differs"))
        }
        if coverage.postedAfterClose > 0 {
            parts.append(String(localized: "\(coverage.postedAfterClose) posted after close"))
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - #25: closing a gap

    /// Only a line the feed never delivered is worth adding. A near-miss
    /// (`amountDiffers`) already HAS a transaction — offering to add it there
    /// would invite a duplicate to fix a disagreement.
    static func canAdd(_ line: Components.Schemas.StatementLine) -> Bool {
        state(of: line) == .missing
    }

    /// #29 + #25: hand this line's values to the add-transaction sheet.
    ///
    /// A PREFILL and nothing more. Building it calls nothing and stores
    /// nothing — this screen is read-only on the ledger, so the household reads
    /// every field and presses Save itself.
    func prefill(for line: Components.Schemas.StatementLine) -> AddTransactionViewModel.Prefill {
        AddTransactionViewModel.Prefill(
            accountID: statement.accountId,
            occurredOn: line.occurredOn,
            // Already in the ledger's convention: the stored line is negative
            // for a charge, so the sheet lands on "Expense" without guessing.
            amountMinor: line.amount.amountMinor,
            merchant: line.description)
    }

    /// "Sapphire · August 2026" — which card and which cycle is being checked,
    /// falling back to the recorded statement while the read is in flight.
    var subtitle: String {
        guard let reconciliation else { return statement.accountName }
        return "\(reconciliation.accountName) · \(reconciliation.periodLabel)"
    }
}
