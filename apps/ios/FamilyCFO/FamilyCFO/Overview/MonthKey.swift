import Foundation

/// "yyyy-MM" month-key arithmetic shared by the Overview month picker and cards.
enum MonthKey {
    private static func calendar() -> Calendar {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .current
        return cal
    }

    static func current() -> String {
        let comps = calendar().dateComponents([.year, .month], from: Date())
        return String(format: "%04d-%02d", comps.year ?? 0, comps.month ?? 0)
    }

    static func shift(_ iso: String, by delta: Int) -> String? {
        let parts = iso.split(separator: "-")
        guard parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]) else {
            return nil
        }
        let cal = calendar()
        guard let base = cal.date(from: DateComponents(year: year, month: month)),
            let shifted = cal.date(byAdding: .month, value: delta, to: base)
        else { return nil }
        let comps = cal.dateComponents([.year, .month], from: shifted)
        return String(format: "%04d-%02d", comps.year ?? 0, comps.month ?? 0)
    }

    /// "July 2026" for display.
    static func label(_ iso: String) -> String {
        let parts = iso.split(separator: "-")
        guard parts.count == 2, let year = Int(parts[0]), let month = Int(parts[1]),
            let date = calendar().date(from: DateComponents(year: year, month: month))
        else { return iso }
        // The date is UTC midnight on the 1st; render it in UTC too. Formatting in
        // the device's local zone (default) rolls a phone behind UTC back to the
        // last day of the previous month — "June" would display as "May".
        var style = Date.FormatStyle.dateTime.month(.wide).year()
        style.timeZone = TimeZone(identifier: "UTC") ?? .current
        return date.formatted(style)
    }
}
