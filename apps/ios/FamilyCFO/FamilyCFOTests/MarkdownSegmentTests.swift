import Testing

@testable import FamilyCFO

struct MarkdownSegmentTests {
    @Test func splitsProseAndTableIntoSegments() {
        let text = """
        #### Step 1

        Trim the biggest habits.

        | Category | Current | Tied to Goal |
        |----------|---------|--------------|
        | Shopping | $1,222.81 | Builds fund |
        | Tennis | $541.88 | Protected |

        Total: **$1,045**.
        """

        let segments = MarkdownSegment.parse(text)

        // prose, table, prose
        #expect(segments.count == 3)
        guard case .markdown(let first) = segments[0] else {
            Issue.record("expected leading prose"); return
        }
        #expect(first.contains("Step 1"))

        guard case .table(let table) = segments[1] else {
            Issue.record("expected a table segment"); return
        }
        #expect(table.headers == ["Category", "Current", "Tied to Goal"])
        #expect(table.rows.count == 2)
        #expect(table.rows[0] == ["Shopping", "$1,222.81", "Builds fund"])
        #expect(table.rows[1] == ["Tennis", "$541.88", "Protected"])

        guard case .markdown(let last) = segments[2] else {
            Issue.record("expected trailing prose"); return
        }
        #expect(last.contains("Total"))
    }

    @Test func plainProseIsASingleMarkdownSegment() {
        let segments = MarkdownSegment.parse("Just some **advice** with no table.")
        #expect(segments.count == 1)
        guard case .markdown(let md) = segments[0] else {
            Issue.record("expected markdown"); return
        }
        #expect(md == "Just some **advice** with no table.")
    }

    @Test func aPipeInProseIsNotMistakenForATable() {
        // No separator row -> not a table.
        let segments = MarkdownSegment.parse("Choose A | B based on cost.")
        #expect(segments.count == 1)
        if case .table = segments[0] {
            Issue.record("prose with a stray pipe must not parse as a table")
        }
    }
}
