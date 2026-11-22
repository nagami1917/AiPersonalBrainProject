import { describe, expect, it } from 'vitest';
import { extractWikiLinks, parseInline, parseMarkdown } from './markdown';

describe('parseInline', () => {
  it('leaves plain text alone', () => {
    expect(parseInline('just words')).toEqual([{ kind: 'text', value: 'just words' }]);
  });

  it('parses bold, italic and code', () => {
    expect(parseInline('a **bold** b')).toEqual([
      { kind: 'text', value: 'a ' },
      { kind: 'bold', value: 'bold' },
      { kind: 'text', value: ' b' }
    ]);
    expect(parseInline('*it*')).toEqual([{ kind: 'italic', value: 'it' }]);
    expect(parseInline('`x`')).toEqual([{ kind: 'code', value: 'x' }]);
  });

  it('parses http and relative links', () => {
    expect(parseInline('[a](https://example.com)')).toEqual([
      { kind: 'link', value: 'a', href: 'https://example.com' }
    ]);
    expect(parseInline('[a](/notes/1)')).toEqual([{ kind: 'link', value: 'a', href: '/notes/1' }]);
  });

  it('refuses to make a javascript: URL clickable', () => {
    // The whole reason this parser exists instead of {@html}. A link that would
    // execute script must degrade to text, never to an anchor.
    const spans = parseInline('[click](javascript:alert(1))');
    expect(spans.some((s) => s.kind === 'link')).toBe(false);
    expect(spans[0]).toMatchObject({ kind: 'text' });
  });

  it('refuses data: URLs too', () => {
    const spans = parseInline('[x](data:text/html,<script>alert(1)</script>)');
    expect(spans.some((s) => s.kind === 'link')).toBe(false);
  });

  it('parses wiki links with and without a label', () => {
    expect(parseInline('[[Target]]')).toEqual([
      { kind: 'wikilink', target: 'Target', label: 'Target' }
    ]);
    expect(parseInline('[[Target|shown]]')).toEqual([
      { kind: 'wikilink', target: 'Target', label: 'shown' }
    ]);
  });

  it('does not lose text it does not understand', () => {
    const spans = parseInline('a **unclosed b');
    const joined = spans.map((s) => ('value' in s ? s.value : '')).join('');
    expect(joined).toContain('unclosed');
  });

  it('does not treat HTML as markup', () => {
    // It becomes text spans, so Svelte renders it as characters.
    const spans = parseInline('<img src=x onerror=alert(1)>');
    expect(spans.every((s) => s.kind === 'text')).toBe(true);
    expect(spans.map((s) => ('value' in s ? s.value : '')).join('')).toContain('<img');
  });
});

describe('parseMarkdown blocks', () => {
  it('parses headings by level', () => {
    const blocks = parseMarkdown('# One\n## Two\n### Three');
    expect(blocks.map((b) => (b.kind === 'heading' ? b.level : null))).toEqual([1, 2, 3]);
  });

  it('keeps a fenced code block verbatim, markdown included', () => {
    const blocks = parseMarkdown('```js\nconst a = "**not bold**";\n```');
    expect(blocks).toEqual([
      { kind: 'code', language: 'js', value: 'const a = "**not bold**";' }
    ]);
  });

  it('parses bullet and numbered lists', () => {
    const bullets = parseMarkdown('- one\n- two');
    expect(bullets[0]).toMatchObject({ kind: 'list', ordered: false });
    const numbers = parseMarkdown('1. one\n2. two');
    expect(numbers[0]).toMatchObject({ kind: 'list', ordered: true });
  });

  it('joins wrapped paragraph lines into one paragraph', () => {
    const blocks = parseMarkdown('first line\nsecond line');
    expect(blocks).toHaveLength(1);
    expect(blocks[0]).toMatchObject({ kind: 'paragraph' });
  });

  it('parses blockquotes and rules', () => {
    expect(parseMarkdown('> quoted')[0]).toMatchObject({ kind: 'quote' });
    expect(parseMarkdown('---')[0]).toMatchObject({ kind: 'rule' });
  });

  it('handles an empty body without producing blocks', () => {
    expect(parseMarkdown('')).toEqual([]);
    expect(parseMarkdown('\n\n  \n')).toEqual([]);
  });

  it('handles an unclosed fence without throwing', () => {
    const blocks = parseMarkdown('```\nstill code');
    expect(blocks).toEqual([{ kind: 'code', language: '', value: 'still code' }]);
  });
});

describe('extractWikiLinks', () => {
  it('matches the server normalisation', () => {
    // These must agree with apps/api extract_wiki_links, or the preview lies.
    expect(extractWikiLinks('[[Q3 Budget]]')).toEqual(['q3 budget']);
    expect(extractWikiLinks('[[Q3 Budget]] [[q3  budget]]')).toEqual(['q3 budget']);
    expect(extractWikiLinks('[[Real Title|label]]')).toEqual(['real title']);
    expect(extractWikiLinks('no links')).toEqual([]);
  });

  it('ignores empty brackets', () => {
    expect(extractWikiLinks('[[]]')).toEqual([]);
  });
});
