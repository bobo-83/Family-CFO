import Foundation

/// Decides when a pause means "I've finished speaking" rather than "I'm
/// thinking" (M87a fix).
///
/// M86 shipped a single flat rule — 1.6 s without the transcript changing means
/// send — and in real use it cut people off mid-sentence: a thinking pause is
/// routinely longer than that, and someone who has just said "...and" is
/// plainly not done no matter how long they hesitate. So how long a silence has
/// to be depends on how finished the sentence sounds.
struct EndOfUtterance: Equatable {
    /// The transcript ends like a completed thought ("...can we afford it?").
    /// A short pause is enough.
    var settled: Duration = .seconds(1.8)

    /// No terminal punctuation — probably mid-thought. Wait noticeably longer
    /// before deciding they're done.
    var unsettled: Duration = .seconds(3.0)

    /// The sentence ends on a word that promises more ("...and", "...because").
    /// Only a long silence should end it — but it must end eventually, because
    /// people do trail off.
    var hangingClause: Duration = .seconds(6.0)

    /// Words that make a sentence sound unfinished. Deliberately conservative:
    /// every one of these is a word almost nobody ends a real question on.
    static let continuationWords: Set<String> = [
        "and", "or", "but", "so", "because", "if", "than", "then", "that",
        "which", "with", "without", "for", "from", "to", "into", "of", "on",
        "in", "at", "by", "about", "over", "under", "the", "a", "an", "my",
        "our", "their", "his", "her", "its", "is", "are", "was", "were", "be",
        "been", "do", "does", "did", "can", "could", "should", "would", "will",
        "um", "uh", "er", "like", "i", "we", "it", "how", "what", "when",
        "where", "why", "who",
    ]

    /// How long the transcript must sit unchanged before the utterance counts
    /// as finished.
    func requiredSilence(after transcript: String) -> Duration {
        let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return unsettled }

        if let last = trimmed.last, ".?!".contains(last) {
            return settled
        }

        let lastWord =
            trimmed
            .split(whereSeparator: { $0.isWhitespace || $0.isPunctuation })
            .last
            .map { String($0).lowercased() } ?? ""

        return Self.continuationWords.contains(lastWord) ? hangingClause : unsettled
    }
}
