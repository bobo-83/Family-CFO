import Foundation

/// Shared display helpers for a transaction row, used by the Categorize tab and
/// the Overview spending drill-down so both surface the same context — the
/// account it came from and the raw bank text — that turns a cryptic line like
/// "Transaction Fee" into something identifiable.
extension Components.Schemas.Transaction {
    /// Source → destination for the account this transaction touches. For a
    /// transfer with a known counterparty, shows both accounts in flow order;
    /// for everything else, just the account it lives in.
    var accountFlow: String? {
        guard let account = accountName, !account.isEmpty else {
            return counterparty.map { "→ \($0)" }
        }
        guard let other = counterparty, !other.isEmpty else { return account }
        return amount.amountMinor < 0 ? "\(account) → \(other)" : "\(other) → \(account)"
    }

    /// The raw bank description, when it adds detail beyond the merchant name
    /// (case-insensitive, so an all-caps echo of the merchant is suppressed).
    var rawDetail: String? {
        guard let description, !description.isEmpty else { return nil }
        return description.caseInsensitiveCompare(merchant ?? "") == .orderedSame ? nil : description
    }

    /// A short, scannable tail of the bank's reference id — enough to tell two
    /// otherwise-identical duplicate legs apart (M97).
    var shortReference: String? {
        guard let externalId, !externalId.isEmpty else { return nil }
        let tail = externalId.suffix(6)
        return tail.isEmpty ? nil : String(tail).uppercased()
    }
}
