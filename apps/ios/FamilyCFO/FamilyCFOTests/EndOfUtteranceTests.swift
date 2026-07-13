import Foundation
import Testing

@testable import FamilyCFO

/// The M86 rule was a flat 1.6 s of quiet, which cut people off mid-sentence in
/// real use (user report, 2026-07-13: "my voice kept being cut off"). How long a
/// pause has to be now depends on how finished the sentence sounds.
struct EndOfUtteranceTests {
    private let rule = EndOfUtterance()

    @Test func afinishedQuestionNeedsOnlyAShortPause() {
        #expect(rule.requiredSilence(after: "Can we afford a new car?") == rule.settled)
        #expect(rule.requiredSilence(after: "Tell me about our budget.") == rule.settled)
    }

    /// The regression itself: a thinking pause mid-thought must NOT be treated
    /// as the end of the question.
    @Test func anUnfinishedThoughtWaitsLonger() {
        let required = rule.requiredSilence(after: "I was thinking about our grocery spending")

        #expect(required == rule.unsettled)
        #expect(required > rule.settled)
    }

    /// "...and" is never the end of a question, however long the speaker
    /// hesitates after it.
    @Test func aDanglingConjunctionWaitsLongest() {
        #expect(rule.requiredSilence(after: "We want to buy a car and") == rule.hangingClause)
        #expect(rule.requiredSilence(after: "I'd like to know if") == rule.hangingClause)
        #expect(rule.requiredSilence(after: "Our savings are, um") == rule.hangingClause)
    }

    /// But it does end eventually — people trail off, and the loop must not
    /// hang forever waiting for a sentence that never lands.
    @Test func evenADanglingClauseEventuallyEnds() {
        #expect(rule.hangingClause < .seconds(30))
    }

    @Test func trailingPunctuationDoesNotHideAContinuationWord() {
        // Recognizers often append a comma after a filler word.
        #expect(rule.requiredSilence(after: "so we were wondering, and,") == rule.hangingClause)
    }

    @Test func everyThresholdIsLongerThanTheRuleThatCutPeopleOff() {
        // The M86 flat threshold. Nothing may be shorter than it now.
        let old = Duration.seconds(1.6)

        #expect(rule.settled > old)
        #expect(rule.unsettled > old)
        #expect(rule.hangingClause > old)
    }
}
