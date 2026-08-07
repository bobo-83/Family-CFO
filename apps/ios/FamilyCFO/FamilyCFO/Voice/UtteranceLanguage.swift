import Foundation
import NaturalLanguage

/// Which supported language a piece of text is actually written in (#10).
/// Read-aloud must speak the language OF THE TEXT, not of the current
/// setting: an existing Vietnamese answer keeps its Vietnamese voice even
/// after the household flips back to English, and an English answer keeps
/// the natural on-box voice in a Vietnamese household (user report,
/// 2026-08-07). Detection runs on device via NLLanguageRecognizer.
///
/// Deliberately NOT `languageConstraints`/`languageHints` (measured
/// 2026-08-07): the recognizer has no Lithuanian class at all, so
/// constraining to [en, vi, lt] funnels Lithuanian text into "vi" at 0.99
/// confidence, and a hints dictionary zeroes every unhinted language.
/// Instead the recognizer runs free and the hypotheses are mapped: the
/// advisor only ever writes en/vi/lt, so a confident guess that is neither
/// English nor Vietnamese — Lithuanian reads to the model as its Slavic,
/// Baltic, or Finnic neighbors — is Lithuanian here.
enum UtteranceLanguage {
    /// First use of NLLanguageRecognizer loads its model (~hundreds of ms on
    /// device, worse on starved CI). Called once from app start on a
    /// background task so the first read-aloud never pays it.
    static func warmUp() {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString("warm up")
        _ = recognizer.languageHypotheses(withMaximum: 1)
    }

    /// Below this the recognizer is guessing, not recognizing. Short strings
    /// and bare numbers carry no language of their own, and a mostly-English
    /// answer citing a diacritic-heavy merchant name ("Phở Hà Nội") can pull
    /// a sub-floor "vi" — both belong to the household setting, not the
    /// detector. Clean single-language answers all measure ≥ 0.97.
    private static let confidenceFloor = 0.85

    /// "en" | "vi" | "lt" for the text; the household language whenever the
    /// recognizer is unsure.
    static func detect(in text: String, householdLanguage: String) -> String {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        let hypotheses = recognizer.languageHypotheses(withMaximum: 10)
        guard !hypotheses.isEmpty else { return householdLanguage }
        // Pool each hypothesis into the supported code it stands for —
        // Lithuanian's probability mass arrives split across its lookalikes.
        var mass: [String: Double] = [:]
        for (language, probability) in hypotheses {
            if let code = code(for: language) {
                mass[code, default: 0] += probability
            }
        }
        guard let best = mass.max(by: { $0.value < $1.value }),
            best.value >= confidenceFloor
        else { return householdLanguage }
        return best.key
    }

    private static func code(for language: NLLanguage) -> String? {
        switch language.rawValue {
        case "en": return "en"
        case "vi": return "vi"
        // How Lithuanian text actually comes back from the model (measured:
        // cs 0.98 / sk 0.97 / cs+sk+hr split on shorter sentences). None of
        // these languages ever appear as a whole advisor answer, so pooling
        // them cannot misfire inside this product's text universe.
        case "lt", "cs", "sk", "hr", "pl", "sl", "fi", "lv", "et", "hu": return "lt"
        default: return nil
        }
    }
}
