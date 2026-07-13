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
}
