import Testing

@testable import FamilyCFO

struct SpokenReplyTests {
    @Test func stripsEmphasisAndCode() {
        #expect(
            SpokenReply.speakable("You can **afford** the `laptop` — *comfortably*.")
                == "You can afford the laptop — comfortably.")
    }

    @Test func keepsLinkLabelsDropsTargets() {
        #expect(
            SpokenReply.speakable("See [your budget](https://box/budgets) for details.")
                == "See your budget for details.")
    }

    @Test func stripsHeadingsAndBullets() {
        let markdown = """
            ## Summary
            - Emergency fund: 4.2 months
            - Savings rate: 18%
            """
        #expect(
            SpokenReply.speakable(markdown)
                == "Summary\nEmergency fund: 4.2 months\nSavings rate: 18%")
    }

    @Test func plainTextPassesThrough() {
        #expect(
            SpokenReply.speakable("Yes — that fits your monthly cash flow of $1,240.")
                == "Yes — that fits your monthly cash flow of $1,240.")
    }

    @Test func preservesArithmeticAsterisksAmidWords() {
        // A lone asterisk between spaces is not markdown emphasis.
        #expect(SpokenReply.speakable("3 * 4 = 12") == "3 * 4 = 12")
    }

    @Test func stripsHorizontalRules() {
        // A --- divider synthesizes to zero bytes and would derail playback.
        let markdown = """
            First part.

            ---

            Second part.
            """
        let spoken = SpokenReply.speakable(markdown)
        #expect(!spoken.contains("---"))
        #expect(spoken.contains("First part."))
        #expect(spoken.contains("Second part."))
    }

    @Test func rateAbbreviationsAreSpokenInFull() {
        // "$500/mo" was read as "slash mo" (user report 2026-07-25).
        #expect(
            SpokenReply.speakable("this frees $500/mo toward your goal")
                == "this frees $500 per month toward your goal")
        #expect(SpokenReply.speakable("about $6,000/yr") == "about $6,000 per year")
        #expect(SpokenReply.speakable("$40/day budget") == "$40 per day budget")
        // A bare path-like slash between words is untouched.
        #expect(SpokenReply.speakable("either/or") == "either/or")
    }

    @Test func sentencesSkipChunksWithNothingToPronounce() {
        // Dividers, arrows, and lone emoji produce empty audio — never sent.
        let chunks = SpokenReply.sentences("Save $488. \n --- \n → \n Then invest.")
        #expect(chunks.allSatisfy { $0.rangeOfCharacter(from: .alphanumerics) != nil })
        #expect(chunks.contains { $0.contains("Save") })
        #expect(chunks.contains { $0.contains("invest") })
    }
}
