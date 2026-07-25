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

import { speakableSentences } from './speakable-text';

describe('speakableSentences (pipelined TTS)', () => {
  it('speaks rate abbreviations in full (user report 2026-07-25)', () => {
    expect(speakableText('this frees $500/mo toward your goal')).toBe(
      'this frees $500 per month toward your goal',
    );
    expect(speakableText('about $6,000/yr')).toBe('about $6,000 per year');
    expect(speakableText('either/or')).toBe('either/or');
  });

  it('splits an answer into sentence chunks', () => {
    const parts = speakableSentences('You can afford it. Save the rest! Any questions?');
    expect(parts.length).toBe(3);
    expect(parts[0]).toContain('afford');
    expect(parts[2]).toContain('questions');
  });

  it('drops chunks with nothing to pronounce', () => {
    const parts = speakableSentences('First part.\n\n---\n\nSecond part.');
    expect(parts.every((p) => /[a-z0-9]/i.test(p))).toBe(true);
    expect(parts.join(' ')).toContain('First part');
    expect(parts.join(' ')).toContain('Second part');
  });

  it('returns empty for empty input', () => {
    expect(speakableSentences('')).toEqual([]);
  });
});
