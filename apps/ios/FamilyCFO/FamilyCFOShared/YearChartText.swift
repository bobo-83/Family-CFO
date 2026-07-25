import Foundation

/// Month labels and the "explain this bar" advisor questions for the Year
/// chart. Shared so the phone and the watch name months — and phrase the
/// grounded questions — identically (ADR 0068).
enum YearChartText {
    static func shortLabel(_ month: String) -> String {
        // "2026-03" -> "Mar"
        guard let number = Int(month.suffix(2)), (1...12).contains(number) else { return month }
        return Calendar.current.shortMonthSymbols[number - 1]
    }

    static func longLabel(_ month: String) -> String {
        // "2026-03" -> "March 2026"
        guard let number = Int(month.suffix(2)), (1...12).contains(number) else { return month }
        return "\(Calendar.current.monthSymbols[number - 1]) \(month.prefix(4))"
    }

    /// Questions the advisor answers from its month-scoped tools
    /// (get_income_and_tax, get_spending_by_category, get_spending_insights).
    static func incomeQuestion(for month: String) -> String {
        "What made up my income in \(longLabel(month))? List where the money came from."
    }

    static func spendingQuestion(for month: String) -> String {
        "What made up my spending in \(longLabel(month))? Break it down by category and biggest merchants."
    }
}
