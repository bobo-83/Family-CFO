import Foundation
import Testing

@testable import FamilyCFO

/// #4: the goal row's funding line — what the ledger shows filling the goal,
/// worded per the server's status. Pure formatter, so the wordings are pinned
/// here rather than eyeballed in the simulator.
@MainActor
struct GoalFundingLineTests {
    private func money(_ minor: Int64) -> Components.Schemas.Money {
        .init(amountMinor: minor, currency: "USD")
    }

    private func goal(
        type: Components.Schemas.GoalType = .vacation,
        funding: Components.Schemas.GoalFunding?
    ) -> Components.Schemas.Goal {
        .init(
            id: "goal-1", name: "Hawaii 2027", _type: type,
            target: money(1_000_000), current: money(250_000), priority: 1,
            funding: funding)
    }

    private func funding(
        monthly: Int64,
        projected: String? = nil,
        status: String,
        fundedBy: [Components.Schemas.GoalFundingSource] = []
    ) -> Components.Schemas.GoalFunding {
        .init(
            monthlyEquivalent: money(monthly), fundedBy: fundedBy,
            projectedCompletion: projected, status: status)
    }

    @Test func onTrackNamesTheRateAndTheProjectedDate() {
        let line = GoalsView.fundingLine(
            goal(funding: funding(monthly: 50_000, projected: "2027-03-15", status: "on_track")))

        #expect(line == "On track — $500.00/mo going in · projected Mar 15, 2027")
    }

    @Test func behindSaysTheProjectionLandsAfterTheTarget() {
        let line = GoalsView.fundingLine(
            goal(funding: funding(monthly: 20_000, projected: "2028-11-02", status: "behind")))

        #expect(line == "Behind — projected Nov 2, 2028, after the target")
    }

    /// Money going in but no target date to judge it against.
    @Test func fundedWithNoDateJustStatesTheRate() {
        let line = GoalsView.fundingLine(
            goal(funding: funding(monthly: 12_550, status: "funded_no_date")))

        #expect(line == "$125.50/mo going in")
    }

    @Test func unfundedSaysSoPlainly() {
        let line = GoalsView.fundingLine(
            goal(funding: funding(monthly: 0, status: "unfunded")))

        #expect(line == "Nothing is currently funding this goal")
    }

    /// The retirement caveat: payroll deductions never reach the bank feed, so
    /// an "unfunded" 401(k) goal is the normal case, not a problem — the line
    /// must explain the absence instead of alarming about it.
    @Test func anUnfundedRetirementGoalExplainsThePayrollBlindSpot() {
        let line = GoalsView.fundingLine(
            goal(type: .retirement, funding: funding(monthly: 0, status: "unfunded")))

        #expect(line == "No linked transfers — 401(k) payroll deductions don't appear here.")
    }

    /// A goal from an older server carries no funding — no line, not a guess.
    @Test func noFundingPayloadMeansNoLine() {
        #expect(GoalsView.fundingLine(goal(funding: nil)) == nil)
    }

    /// A status this build doesn't know stays silent rather than mislabeling.
    @Test func anUnknownStatusShowsNothing() {
        #expect(
            GoalsView.fundingLine(
                goal(funding: funding(monthly: 10_000, status: "recalculating"))) == nil)
    }
}
