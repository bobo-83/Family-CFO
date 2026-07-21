import { speakableText } from './speakable-text';

describe('speakableText', () => {
  it('strips headings, emphasis, and bullets', () => {
    const out = speakableText('#### Step 1\n\n- Spend **less** and _save_ more.');
    expect(out).toContain('Step 1');
    expect(out).toContain('Spend less and save more.');
    expect(out).not.toContain('#');
    expect(out).not.toContain('*');
    expect(out).not.toContain('-');
  });

  it('flattens a table into spoken rows', () => {
    const md = ['| Category | Saving |', '|---|---|', '| Shopping | $488 |'].join('\n');
    const out = speakableText(md);
    expect(out).not.toContain('|');
    expect(out).not.toContain('---');
    expect(out).toContain('Shopping, $488');
  });

  it('keeps link text but drops the URL', () => {
    expect(speakableText('See [the budget](https://x.example/y).')).toBe('See the budget.');
  });
});
