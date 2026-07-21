import MarkdownUI
import SwiftUI

/// Renders an advisor answer. Prose (headings, lists, bold) renders through
/// MarkdownUI; GitHub-flavored **tables render as stacked cards** — one card per
/// row, first column as the title and the rest as labeled fields — because a
/// wide grid can't fit a phone-width bubble (ADR 0051).
struct MarkdownMessageView: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(MarkdownSegment.parse(text).enumerated()), id: \.offset) { _, segment in
                switch segment {
                case .markdown(let md):
                    Markdown(md)
                        .markdownTheme(.chatBubble)
                        .frame(maxWidth: .infinity, alignment: .leading)
                case .table(let table):
                    TableCards(table: table)
                }
            }
        }
    }
}

/// A parsed GitHub-flavored table: the header labels and the body rows.
struct MarkdownTable {
    let headers: [String]
    let rows: [[String]]
}

/// The advisor's answer split into runs of prose and tables, so each renders the
/// way it should.
enum MarkdownSegment {
    case markdown(String)
    case table(MarkdownTable)

    static func parse(_ text: String) -> [MarkdownSegment] {
        let lines = text.components(separatedBy: "\n")
        var segments: [MarkdownSegment] = []
        var prose: [String] = []

        func flushProse() {
            let joined = prose.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { segments.append(.markdown(joined)) }
            prose.removeAll()
        }

        var i = 0
        while i < lines.count {
            let header = lines[i]
            let separator = i + 1 < lines.count ? lines[i + 1] : nil
            if header.contains("|"), !isSeparator(header), let separator, isSeparator(separator) {
                flushProse()
                let headers = cells(header)
                var rows: [[String]] = []
                var j = i + 2
                while j < lines.count,
                    lines[j].contains("|"),
                    !lines[j].trimmingCharacters(in: .whitespaces).isEmpty,
                    !isSeparator(lines[j])
                {
                    rows.append(cells(lines[j]))
                    j += 1
                }
                segments.append(.table(MarkdownTable(headers: headers, rows: rows)))
                i = j
            } else {
                prose.append(header)
                i += 1
            }
        }
        flushProse()
        return segments
    }

    /// A markdown table separator row: every cell is dashes with optional colons.
    private static func isSeparator(_ line: String) -> Bool {
        guard line.contains("|") else { return false }
        let parts = cells(line)
        guard !parts.isEmpty else { return false }
        return parts.allSatisfy { cell in
            let trimmed = cell.replacingOccurrences(of: " ", with: "")
            return !trimmed.isEmpty && trimmed.allSatisfy { $0 == "-" || $0 == ":" } && trimmed.contains("-")
        }
    }

    /// Split a `| a | b |` row into trimmed cell strings.
    private static func cells(_ line: String) -> [String] {
        var s = line.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("|") { s.removeFirst() }
        if s.hasSuffix("|") { s.removeLast() }
        return s.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
    }
}

/// One card per table row: the first column is the title, each other column a
/// labeled field. Inline markdown in cells (e.g. **$488.81**) is preserved.
private struct TableCards: View {
    let table: MarkdownTable

    var body: some View {
        VStack(spacing: 8) {
            ForEach(Array(table.rows.enumerated()), id: \.offset) { _, row in
                VStack(alignment: .leading, spacing: 6) {
                    if let title = row.first, !title.isEmpty {
                        Text(markdown: title)
                            .font(.subheadline.weight(.semibold))
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    ForEach(Array(table.headers.enumerated()), id: \.offset) { index, label in
                        if index >= 1, index < row.count, !row[index].isEmpty {
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(label)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Spacer(minLength: 8)
                                Text(markdown: row[index])
                                    .font(.subheadline)
                                    .multilineTextAlignment(.trailing)
                            }
                        }
                    }
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.background.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(.secondary.opacity(0.25), lineWidth: 1)
                )
            }
        }
    }
}
