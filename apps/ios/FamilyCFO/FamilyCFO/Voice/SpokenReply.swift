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

    /// Splits a spoken answer into sentence-sized chunks (M87). The on-box
    /// voice synthesizes one sentence while the next is still being fetched,
    /// so the user waits for ONE sentence to synthesize rather than the whole
    /// answer — the difference between a natural voice and a slow one.
    static func sentences(_ text: String) -> [String] {
        var chunks: [String] = []
        text.enumerateSubstrings(in: text.startIndex..., options: [.bySentences]) {
            substring, _, _, _ in
            let sentence = substring?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            if !sentence.isEmpty {
                chunks.append(sentence)
            }
        }
        if chunks.isEmpty {
            let whole = text.trimmingCharacters(in: .whitespacesAndNewlines)
            return whole.isEmpty ? [] : [whole]
        }
        return chunks
    }
}
