import Foundation

/// Turns the advisor's markdown answer into text worth speaking aloud —
/// the synthesizer would otherwise read "asterisk asterisk" and URLs.
enum SpokenReply {
    static func speakable(_ markdown: String) -> String {
        var text = markdown
        // Links: keep the label, drop the target.
        text = text.replacingOccurrences(
            of: #"\[([^\]]+)\]\([^)]*\)"#, with: "$1", options: .regularExpression)
        // Emphasis/code markers.
        for marker in ["**", "__", "`"] {
            text = text.replacingOccurrences(of: marker, with: "")
        }
        // Single-marker emphasis pairs hugging their content (*word*,
        // _phrase here_) — a lone asterisk between spaces (arithmetic)
        // never matches.
        for marker in ["*", "_"] {
            let escaped = NSRegularExpression.escapedPattern(for: marker)
            text = text.replacingOccurrences(
                of: "\(escaped)(\\S(?:[^\(escaped)\\n]*\\S)?)\(escaped)",
                with: "$1", options: .regularExpression)
        }
        // Headings and bullets at line starts.
        text = text.replacingOccurrences(
            of: #"(?m)^\s*#{1,6}\s*"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(
            of: #"(?m)^\s*[-*•]\s+"#, with: "", options: .regularExpression)
        // Collapse leftover whitespace runs.
        text = text.replacingOccurrences(
            of: #"[ \t]{2,}"#, with: " ", options: .regularExpression)
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
