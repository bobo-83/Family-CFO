import { MarkdownPipe } from './markdown.pipe';

describe('MarkdownPipe', () => {
  const pipe = new MarkdownPipe();

  it('renders a GitHub-flavored table as HTML', () => {
    const md = ['| A | B |', '|---|---|', '| 1 | 2 |'].join('\n');
    const html = pipe.transform(md);
    expect(html).toContain('<table>');
    expect(html).toContain('<th>A</th>');
    expect(html).toContain('<td>1</td>');
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
