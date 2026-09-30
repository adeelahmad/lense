/**
 * Search snippets arrive as HTML: the backend escapes the segment text and wraps matches in <mark>. They are turned
 * into plain parts here and rendered as React text, so nothing from a transcript is ever parsed as markup.
 */

export type SnippetPart = { text: string; mark: boolean };

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

/** Undo HTML escaping (named, decimal and hex entities); unknown entities stay as written. */
export function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (all, code: string) => {
    if (code[0] === "#") {
      const n = code[1].toLowerCase() === "x" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(n) && n > 0 && n < 0x110000 ? String.fromCodePoint(n) : all;
    }
    return ENTITIES[code.toLowerCase()] ?? all;
  });
}

/** "a <mark>b</mark> c" → [{a}, {b, mark}, {c}], with entities decoded. Stray tags other than mark are kept as text. */
export function splitSnippet(snippet: string | null | undefined): SnippetPart[] {
  const s = snippet ?? "";
  const out: SnippetPart[] = [];
  const re = /<mark>([\s\S]*?)<\/mark>/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: decodeEntities(s.slice(last, m.index)), mark: false });
    if (m[1]) out.push({ text: decodeEntities(m[1]), mark: true });
    last = re.lastIndex;
  }
  if (last < s.length) out.push({ text: decodeEntities(s.slice(last)), mark: false });
  return out;
}

/** The snippet as plain text (for labels and quotes). */
export function snippetText(snippet: string | null | undefined): string {
  return splitSnippet(snippet)
    .map((p) => p.text)
    .join("");
}
