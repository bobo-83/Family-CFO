import Foundation

extension ISO8601DateFormatter {
    /// Parses ISO-8601 with or without fractional seconds (the API emits
    /// both, depending on whether the timestamp has sub-second precision).
    static func lenientDate(from string: String) -> Date? {
        if let date = fractional.date(from: string) { return date }
        return plain.date(from: string)
    }

    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let plain: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}
