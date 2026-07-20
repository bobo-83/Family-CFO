import Foundation

/// Drives the Review tab (M97): exact-duplicate charges grouped so the user can
/// keep them (a real repeat), dispute one, or delete an erroneous one. Owned by
/// the tab shell so its `reviewCount` can badge the tab and stay live as items
/// clear.
@MainActor
@Observable
final class ReviewViewModel {
    private let api: ReviewAPI

    private(set) var groups: [ReviewGroup] = []
    private(set) var transfers: [Components.Schemas.Transaction] = []
    private(set) var credits: [Components.Schemas.Transaction] = []
    /// ADR 0049: transfers that look like misfiled income, awaiting the user's
    /// confirm-as-income / keep-as-transfer decision.
    private(set) var suspectedIncome: [Components.Schemas.Transaction] = []
    private(set) var categories: [Components.Schemas.Category] = []
    private(set) var isLoading = false
    private(set) var errorMessage: String?

    init(api: ReviewAPI) { self.api = api }

    /// One set of identical charges (same account, date, amount, merchant).
    struct ReviewGroup: Identifiable {
        let id: String
        let transactions: [Components.Schemas.Transaction]
        var sample: Components.Schemas.Transaction { transactions[0] }
        var count: Int { transactions.count }
        var hasDisputed: Bool { transactions.contains { $0.duplicateState == .disputed } }
    }

    /// Duplicate/disputed transactions awaiting action — drives the tab badge.
    /// (Transfers and credits are always available for optional review, so they
    /// don't inflate the badge.)
    var reviewCount: Int { groups.reduce(0) { $0 + $1.count } }
    var isEmpty: Bool { groups.isEmpty }
    var nothingToReview: Bool {
        groups.isEmpty && transfers.isEmpty && credits.isEmpty && suspectedIncome.isEmpty
    }

    /// Transfers/credits grouped by amount — the two legs of one transfer (an
    /// out and a matching in, same size) collapse together, as do repeats.
    struct AmountGroup: Identifiable {
        let id: String
        let transactions: [Components.Schemas.Transaction]
        var sample: Components.Schemas.Transaction { transactions[0] }
        var count: Int { transactions.count }
        var absAmount: Int64 { abs(sample.amount.amountMinor) }
    }

    var transferGroups: [AmountGroup] { Self.groupByAmount(transfers) }
    var creditGroups: [AmountGroup] { Self.groupByAmount(credits) }

    static func groupByAmount(_ txns: [Components.Schemas.Transaction]) -> [AmountGroup] {
        var buckets: [Int64: [Components.Schemas.Transaction]] = [:]
        for t in txns { buckets[abs(t.amount.amountMinor), default: []].append(t) }
        return buckets
            .map {
                AmountGroup(
                    id: "\($0.key)",
                    transactions: $0.value.sorted { $0.occurredAt > $1.occurredAt })
            }
            .sorted { $0.absAmount > $1.absAmount }
    }

    /// Identical if these four match — the same rule the server flags on.
    static func key(_ t: Components.Schemas.Transaction) -> String {
        "\(t.accountId)|\(t.amount.amountMinor)|\(t.occurredAt)|\(t.merchant ?? t.description ?? "")"
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let dupes = api.queue(kind: .duplicates)
            async let xfers = api.queue(kind: .transfers)
            async let creds = api.queue(kind: .credits)
            async let suspected = api.queue(kind: .suspectedIncome)
            async let cats = api.categories()
            let queue = try await dupes
            errorMessage = nil
            var buckets: [String: [Components.Schemas.Transaction]] = [:]
            for txn in queue { buckets[Self.key(txn), default: []].append(txn) }
            groups =
                buckets
                .map { ReviewGroup(id: $0.key, transactions: $0.value.sorted { $0.id < $1.id }) }
                // Disputed items surface first (they're being actively tracked),
                // then by size of the charge.
                .sorted { lhs, rhs in
                    if lhs.hasDisputed != rhs.hasDisputed { return lhs.hasDisputed }
                    return abs(lhs.sample.amount.amountMinor) > abs(rhs.sample.amount.amountMinor)
                }
            transfers = (try await xfers).sorted { $0.occurredAt > $1.occurredAt }
            credits = (try await creds).sorted { $0.occurredAt > $1.occurredAt }
            suspectedIncome = (try await suspected).sorted {
                abs($0.amount.amountMinor) > abs($1.amount.amountMinor)
            }
            categories = try await cats
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }

    /// The last recategorization (one transaction or a whole group), so it can be
    /// undone if it was a mistake — each transaction restored to its prior category.
    struct Undo {
        let items: [(id: String, previous: String?)]
        let label: String
        var id: String { items.map(\.id).joined(separator: ",") }
    }
    private(set) var lastUndo: Undo?

    /// Recategorize one transfer/credit, remembering its prior category.
    func recategorize(_ txn: Components.Schemas.Transaction, to categoryID: String) async {
        await recategorizeMany(
            [txn], to: categoryID, label: txn.merchant ?? txn.description ?? "transaction")
    }

    /// Recategorize a whole group at once (e.g. all seven $300 Zelle transfers).
    func recategorizeGroup(_ group: AmountGroup, to categoryID: String) async {
        await recategorizeMany(
            group.transactions, to: categoryID,
            label: "\(group.count) × \(group.sample.merchant ?? group.sample.description ?? "transaction")")
    }

    private func recategorizeMany(
        _ txns: [Components.Schemas.Transaction], to categoryID: String, label: String
    ) async {
        let undo = Undo(items: txns.map { ($0.id, $0.categoryId) }, label: label)
        await mutate {
            for txn in txns {
                try await self.api.setCategory(transactionID: txn.id, categoryID: categoryID)
            }
        }
        if errorMessage == nil { lastUndo = undo }
    }

    /// ADR 0049: confirm a suspected transfer really is income — refile it under
    /// the Income category (creating one if the household has none). Undoable via
    /// the same snackbar as any recategorization.
    func confirmAsIncome(_ txn: Components.Schemas.Transaction) async {
        let incomeID: String
        if let existing = categories.first(where: { $0.name.lowercased() == "income" }) {
            incomeID = existing.id
        } else {
            do {
                incomeID = try await api.createCategory(name: "Income").id
            } catch {
                errorMessage = ChatViewModel.describe(error)
                return
            }
        }
        await recategorizeMany(
            [txn], to: incomeID, label: txn.merchant ?? txn.description ?? "income")
    }

    /// ADR 0049: keep it as a transfer — record an exclude override so it's never
    /// flagged as suspected income again.
    func keepAsTransfer(_ txn: Components.Schemas.Transaction) async {
        await mutate { try await self.api.keepAsTransfer(transactionID: txn.id) }
    }

    func undoRecategorize() async {
        guard let undo = lastUndo else { return }
        lastUndo = nil
        await mutate {
            for item in undo.items {
                try await self.api.setCategory(
                    transactionID: item.id, categoryID: item.previous)
            }
        }
    }

    func clearUndo() { lastUndo = nil }

    /// Delete a category from the shared picker (long-press) — the server
    /// un-categorizes its transactions; reload so the queues reflect it.
    func deleteCategory(id: String) async {
        await mutate { try await self.api.deleteCategory(id: id) }
    }

    /// Pull-to-refresh: sync the banks first, then reload the queues — same
    /// gesture as every other tab (M103).
    func sync() async {
        await mutate { try await self.api.syncBanks() }
    }

    /// A legitimate repeat — dismiss the whole group so it never re-flags.
    func keepAll(_ group: ReviewGroup) async {
        await mutate {
            for txn in group.transactions {
                try await self.api.setState(transactionID: txn.id, state: .dismissed)
            }
        }
    }

    func dispute(_ txn: Components.Schemas.Transaction) async {
        await mutate { try await self.api.setState(transactionID: txn.id, state: .disputed) }
    }

    /// Stop tracking a dispute (resolved) — keep the transaction, clear the flag.
    func resolveDispute(_ txn: Components.Schemas.Transaction) async {
        await mutate { try await self.api.setState(transactionID: txn.id, state: .dismissed) }
    }

    func delete(_ txn: Components.Schemas.Transaction) async {
        await mutate { try await self.api.delete(transactionID: txn.id) }
    }

    private func mutate(_ work: () async throws -> Void) async {
        do {
            try await work()
            await load()
        } catch {
            errorMessage = ChatViewModel.describe(error)
        }
    }
}
