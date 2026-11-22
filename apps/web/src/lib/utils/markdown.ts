/**
 * A deliberately small Markdown parser.
 *
 * Why not a library: note bodies are user content, and the usual shortcut for
 * rendering Markdown in Svelte is `{@html …}` after a library pass. That puts
 * sanitisation on the critical path — one bad regex and a note becomes XSS.
 * This parser instead returns *data* (blocks and inline spans) that the
 * component renders with real Svelte elements. There is no HTML string anywhere,
 * so there is nothing to sanitise and no `{@html}` in the codebase.
 *
 * Supported on purpose — the subset an author of notes actually uses:
 *   headings (#…######), paragraphs, fenced code, blockquotes, bullet and
 *   numbered lists, horizontal rules, and inline **bold**, *italic*,
 *   `code`, [text](url), and [[wiki links]].
 *
 * Not supported, to keep the surface honest: tables, images, HTML, footnotes.
 * Anything unrecognised is rendered as literal text, never dropped — losing a
 * user's words because the parser did not understand them would be worse than
 * showing the syntax.
 */

export type InlineSpan =
  | { kind: 'text'; value: string }
  | { kind: 'bold'; value: string }
  | { kind: 'italic'; value: string }
  | { kind: 'code'; value: string }
  | { kind: 'link'; value: string; href: string }
  /** `[[Target]]` / `[[Target|label]]` — resolved by the caller against backlinks. */
  | { kind: 'wikilink'; target: string; label: string };

export type Block =
  | { kind: 'heading'; level: number; spans: InlineSpan[] }
  | { kind: 'paragraph'; spans: InlineSpan[] }
  | { kind: 'code'; language: string; value: string }
  | { kind: 'quote'; spans: InlineSpan[] }
  | { kind: 'list'; ordered: boolean; items: InlineSpan[][] }
  | { kind: 'rule' };

const WIKILINK = /^\[\[([^\[\]|]+)(?:\|([^\[\]]*))?\]\]/;
const LINK = /^\[([^\]]+)\]\(([^)\s]+)\)/;
const BOLD = /^(\*\*|__)(.+?)\1/;
const ITALIC = /^(\*|_)(.+?)\1/;
const CODE = /^`([^`]+)`/;

/** Split one line into styled spans. Never throws: unknown input is text. */
export function parseInline(text: string): InlineSpan[] {
  const spans: InlineSpan[] = [];
  let buffer = '';
  let rest = text;

  const flush = () => {
    if (buffer) {
      spans.push({ kind: 'text', value: buffer });
      buffer = '';
    }
  };

  while (rest.length > 0) {
    const wiki = WIKILINK.exec(rest);
    if (wiki) {
      flush();
      spans.push({ kind: 'wikilink', target: wiki[1].trim(), label: (wiki[2] ?? wiki[1]).trim() });
      rest = rest.slice(wiki[0].length);
      continue;
    }

    const link = LINK.exec(rest);
    if (link) {
      flush();
      // Only http(s) and relative links become anchors. `javascript:` in a note
      // body must never become a clickable href.
      const safe = /^(https?:\/\/|\/|#|mailto:)/i.test(link[2]);
      if (safe) spans.push({ kind: 'link', value: link[1], href: link[2] });
      else buffer += link[0];
      rest = rest.slice(link[0].length);
      continue;
    }

    const code = CODE.exec(rest);
    if (code) {
      flush();
      spans.push({ kind: 'code', value: code[1] });
      rest = rest.slice(code[0].length);
      continue;
    }

    const bold = BOLD.exec(rest);
    if (bold) {
      flush();
      spans.push({ kind: 'bold', value: bold[2] });
      rest = rest.slice(bold[0].length);
      continue;
    }

    const italic = ITALIC.exec(rest);
    if (italic) {
      flush();
      spans.push({ kind: 'italic', value: italic[2] });
      rest = rest.slice(italic[0].length);
      continue;
    }

    buffer += rest[0];
    rest = rest.slice(1);
  }

  flush();
  return spans;
}

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^[-*+]\s+(.*)$/;
const NUMBERED = /^\d+[.)]\s+(.*)$/;
const RULE = /^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$/;

/** Block-level parse. One pass, no recursion, no state beyond the current block. */
export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (line.trim() === '') {
      index += 1;
      continue;
    }

    // Fenced code: everything to the closing fence is verbatim.
    const fence = /^```\s*([\w+-]*)\s*$/.exec(line);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        body.push(lines[index]);
        index += 1;
      }
      index += 1; // consume the closing fence
      blocks.push({ kind: 'code', language: fence[1] ?? '', value: body.join('\n') });
      continue;
    }

    if (RULE.test(line.trim())) {
      blocks.push({ kind: 'rule' });
      index += 1;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({
        kind: 'heading',
        level: heading[1].length,
        spans: parseInline(heading[2].trim())
      });
      index += 1;
      continue;
    }

    if (line.startsWith('>')) {
      const body: string[] = [];
      while (index < lines.length && lines[index].startsWith('>')) {
        body.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      blocks.push({ kind: 'quote', spans: parseInline(body.join(' ').trim()) });
      continue;
    }

    const ordered = NUMBERED.test(line);
    if (ordered || BULLET.test(line)) {
      const items: InlineSpan[][] = [];
      // Keep consuming until a line is neither a list item nor an indented
      // continuation, so a wrapped bullet does not split into two lists.
      while (index < lines.length) {
        const current = lines[index];
        const bullet = BULLET.exec(current);
        const number = NUMBERED.exec(current);
        if (ordered && number) {
          items.push(parseInline(number[1].trim()));
        } else if (!ordered && bullet) {
          items.push(parseInline(bullet[1].trim()));
        } else if (current.trim() === '') {
          break;
        } else {
          break;
        }
        index += 1;
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    // Paragraph: consecutive non-blank lines that start no other block.
    const paragraph: string[] = [];
    while (index < lines.length) {
      const current = lines[index];
      if (
        current.trim() === '' ||
        HEADING.test(current) ||
        current.startsWith('>') ||
        current.startsWith('```') ||
        BULLET.test(current) ||
        NUMBERED.test(current) ||
        RULE.test(current.trim())
      ) {
        break;
      }
      paragraph.push(current.trim());
      index += 1;
    }
    blocks.push({ kind: 'paragraph', spans: parseInline(paragraph.join(' ')) });
  }

  return blocks;
}

/**
 * The `[[titles]]` a body links to, normalised exactly as the server does.
 *
 * Kept in step with `apps/api/app/repositories/knowledge.py::extract_wiki_links`
 * on purpose: the editor previews these locally while typing, and a mismatch
 * would make the preview promise a link the save then discards.
 */
export function extractWikiLinks(source: string): string[] {
  const found = new Set<string>();
  const pattern = /\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(source)) !== null) {
    const title = match[1].split(/\s+/).filter(Boolean).join(' ');
    if (title) found.add(title.toLowerCase());
  }
  return [...found];
}
