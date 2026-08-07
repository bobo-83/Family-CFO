import Foundation
import Testing

@testable import FamilyCFO

/// #5: the pure informational-vs-reserved decision behind the Safe-to-spend
/// committed-savings row. No view — just the branch the server's fields pick.
struct CommittedSavingsPresentationTests {
    private func money(_ minor: Int64) -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: "USD")
    }

    private func safeToSpend(
        committed: Int64?,
        reserved: Bool?,
        items: [Components.Schemas.NamedAmount] = []
    ) -> Components.Schemas.SafeToSpend {
        .init(
            liquidBalance: money(100_000),
            emergencyFundReserved: money(0),
            billsDue: money(0),
            minimumDebtPayments: money(0),
            committedTotal: money(0),
            safeToSpend: money(100_000),
            totalDebt: money(0),
            warnings: [],
            committedSavings: committed.map { .init(value1: money($0)) },
            committedSavingsItems: items.isEmpty ? nil : items,
            committedSavingsReserved: reserved)
    }

    private let items = [
        Components.Schemas.NamedAmount(
            name: "College 529 — due Aug 12",
            amount: Components.Schemas.Money(amountMinor: 50_000, currency: "USD"))
    ]

    /// Nothing committed in the horizon → no row at all.
    @Test func absentCommittedSavingsRendersNothing() {
        let presentation = CommittedSavingsPresentation(
            safeToSpend(committed: nil, reserved: nil))
        #expect(presentation == .none)
    }

    /// A zero amount is treated as nothing to show.
    @Test func zeroCommittedSavingsRendersNothing() {
        let presentation = CommittedSavingsPresentation(
            safeToSpend(committed: 0, reserved: false))
        #expect(presentation == .none)
    }

    /// Reserved off (the default) → shown beside, never subtracted.
    @Test func reservedOffIsInformational() {
        let presentation = CommittedSavingsPresentation(
            safeToSpend(committed: 50_000, reserved: false, items: items))
        #expect(presentation == .informational(amount: money(50_000), items: items))
    }

    /// A context from before the flag shipped omits it — default (informational).
    @Test func missingReservedFlagIsInformational() {
        let presentation = CommittedSavingsPresentation(
            safeToSpend(committed: 50_000, reserved: nil, items: items))
        #expect(presentation == .informational(amount: money(50_000), items: items))
    }

    /// Reserved on → subtracted like a bill, listed among the committed rows.
    @Test func reservedOnIsReserved() {
        let presentation = CommittedSavingsPresentation(
            safeToSpend(committed: 50_000, reserved: true, items: items))
        #expect(presentation == .reserved(amount: money(50_000), items: items))
    }
}
