/**
 * Turns the advisor's markdown answer into text worth speaking aloud — strips
 * the markup that a TTS voice would otherwise read as noise (hashes, pipes,
 * asterisks, link URLs). Mirrors the iOS `SpokenReply.speakable` intent.
 */
export function speakableText(markdown: string): string {
  let text = markdown;

  // Code fences and inline code -> their contents.
  text = text.replace(/```[\s\S]*?```/g, (m) => m.replace(/```[a-zA-Z]*\n?/g, ''));
  text = text.replace(/`([^`]+)`/g, '$1');

  // Images -> alt text; links -> link text.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');

  // Table rows: drop the |---| separators; turn "| a | b |" into "a, b".
  text = text
    .split('\n')
    .filter((line) => !/^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line))
    .map((line) => {
      if (line.includes('|')) {
        return line
          .replace(/^\s*\|/, '')
          .replace(/\|\s*$/, '')
          .split('|')
          .map((c) => c.trim())
          .filter(Boolean)
          .join(', ');
      }
      return line;
    })
    .join('\n');

  // Headings, blockquotes, list bullets, horizontal rules.
  text = text.replace(/^\s{0,3}#{1,6}\s*/gm, '');
  text = text.replace(/^\s{0,3}>\s?/gm, '');
  text = text.replace(/^\s*[-*+]\s+/gm, '');
  text = text.replace(/^\s*\d+\.\s+/gm, '');
  text = text.replace(/^\s*([-*_]\s*){3,}\s*$/gm, '');

  // Emphasis markers around text.
  text = text.replace(/(\*\*\*|\*\*|\*|___|__|_|~~)(\S(?:[^*_~\n]*\S)?)\1/g, '$2');

  // Collapse the whitespace left behind.
  return text
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '\n')
    .trim();
}

/**
 * Sentence-sized chunks for pipelined TTS (mirrors iOS `SpokenReply.sentences`):
 * the voice service synthesizes one sentence while the previous plays, so the
 * user waits for ONE sentence, not the whole answer. Chunks with nothing to
 * pronounce (bare dashes/arrows) are dropped — they synthesize to empty audio.
 */
export function speakableSentences(markdown: string): string[] {
  const text = speakableText(markdown);
  if (!text) {
    return [];
  }
  const parts: string[] = [];
  if (typeof Intl !== 'undefined' && 'Segmenter' in Intl) {
    const segmenter = new Intl.Segmenter(undefined, { granularity: 'sentence' });
    for (const segment of segmenter.segment(text)) {
      parts.push(segment.segment);
    }
  } else {
    // Fallback: split on sentence enders and newlines.
    parts.push(...text.split(/(?<=[.!?…])\s+|\n+/));
  }
  return parts.map((p) => p.trim()).filter((p) => /[\p{L}\p{N}]/u.test(p));
}
