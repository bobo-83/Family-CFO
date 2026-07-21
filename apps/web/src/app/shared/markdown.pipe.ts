import { Pipe, PipeTransform } from '@angular/core';
import { marked } from 'marked';

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|')) s = s.slice(0, -1);
  return s.split('|').map((c) => c.trim());
}

function isSeparatorRow(line: string): boolean {
  if (!line.includes('|')) {
    return false;
  }
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c.replace(/\s/g, '')));
}

function inline(md: string): string {
  return marked.parseInline(md, { async: false }) as string;
}

function renderCards(headers: string[], rows: string[][]): string {
  const cards = rows
    .map((row) => {
      const title = inline(row[0] ?? '');
      const fields = row
        .slice(1)
        .map((cell, idx) => {
          if (!cell) {
            return '';
          }
          const label = headers[idx + 1] ?? '';
          return (
            `<div class="md-card__field">` +
            `<span class="md-card__k">${escapeHtml(label)}</span>` +
            `<span class="md-card__v">${inline(cell)}</span>` +
            `</div>`
          );
        })
        .join('');
      return `<div class="md-card"><div class="md-card__title">${title}</div>${fields}</div>`;
    })
    .join('');
  return `\n<div class="md-cards">${cards}</div>\n`;
}

// ADR 0051: wide data tables don't fit a phone-width chat bubble — every cell
// wraps mid-word. Rewrite each GFM table into stacked, labeled cards (first
// column = title, rest = "label: value") before markdown rendering, so it reads
// cleanly at any width and matches the iOS card layout. Raw HTML passes through
// marked untouched; cell content is rendered as inline markdown.
function tablesToCards(md: string): string {
  const lines = md.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const header = lines[i];
    const separator = lines[i + 1];
    const isTable =
      header.includes('|') &&
      !isSeparatorRow(header) &&
      separator !== undefined &&
      isSeparatorRow(separator);
    if (isTable) {
      const headers = splitRow(header);
      const rows: string[][] = [];
      let j = i + 2;
      while (
        j < lines.length &&
        lines[j].includes('|') &&
        lines[j].trim() !== '' &&
        !isSeparatorRow(lines[j])
      ) {
        rows.push(splitRow(lines[j]));
        j += 1;
      }
      out.push(renderCards(headers, rows));
      i = j;
    } else {
      out.push(header);
      i += 1;
    }
  }
  return out.join('\n');
}

/**
 * Renders the advisor's GitHub-flavored markdown (headings, lists, and tables —
 * the last as cards, see above) to HTML for `[innerHTML]`, which Angular
 * sanitizes: scripts/handlers are stripped, structure kept. Pure, so it parses
 * each distinct answer once. Used only for assistant text, never raw user input.
 */
@Pipe({ name: 'markdown' })
export class MarkdownPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    return marked.parse(tablesToCards(value), { async: false, gfm: true, breaks: false }) as string;
  }
}
