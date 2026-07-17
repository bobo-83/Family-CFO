import Foundation
import Testing

@testable import FamilyCFO

/// M115: "N payments remaining" and an end date are two entries for ONE stored
/// fact (the maturity date) — the round trip must agree.
struct LoanDateTests {
    @Test func paymentsLeftRoundTripsThroughTheDerivedDate() {
        let derived = LoanDate.dateAfter(payments: 36)
        #expect(LoanDate.monthsLeft(LoanDate.iso(from: derived)) == 36)
    }

    @Test func zeroAndNegativePaymentsClampToToday() {
        let cal = Calendar.current
        #expect(cal.isDateInToday(LoanDate.dateAfter(payments: 0)))
        #expect(cal.isDateInToday(LoanDate.dateAfter(payments: -3)))
    }

    @Test func monthsLeftOnAPastDateIsZero() {
        let past = Calendar.current.date(byAdding: .month, value: -2, to: Date())!
        #expect(LoanDate.monthsLeft(LoanDate.iso(from: past)) == 0)
    }
}
