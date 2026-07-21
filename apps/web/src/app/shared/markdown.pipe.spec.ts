import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
  const pipe = new MarkdownPipe();

  it('renders a GitHub-flavored table as labeled cards (ADR 0051)', () => {
    const md = [
      '| Category | Current | Goal |',
      '|---|---|---|',
      '| Shopping | $1,222 | Save more |',
    ].join('\n');
    const html = pipe.transform(md);
    // First column is the card title; the rest become "label: value" fields.
    expect(html).toContain('md-cards');
    expect(html).toContain('md-card__title');
    expect(html).toContain('Shopping');
    expect(html).toContain('Current');
    expect(html).toContain('$1,222');
    expect(html).toContain('Goal');
    // No raw wide table.
    expect(html).not.toContain('<table>');
  });

  it('renders headings and bold', () => {
    const html = pipe.transform('#### Step 1\n\nSpend **less**.');
    expect(html).toContain('<h4>Step 1</h4>');
    expect(html).toContain('<strong>less</strong>');
  });

  it('is empty for null/empty input', () => {
    expect(pipe.transform(null)).toBe('');
    expect(pipe.transform('')).toBe('');
  });
});
