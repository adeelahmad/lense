/**
 * The search box: free words plus typed filters (`speaker:"Host B"`, `namespace:podcasts`, `emotion:surprise`,
 * `recording:"Episode 12"`). Typed filters become chips; the chip and the facet are one filter. The backend's rules:
 * every word must appear (English stemming), "quoted phrases" as written, OR between alternatives, no prefix search.
 */

export type FilterKey = "namespace" | "speaker" | "emotion" | "recording";

/** Filters as the URL holds them: speaker and recording are ids, namespace a name, emotion a label. */
export type SearchFilters = {
  namespace?: string;
  speaker?: number;
  emotion?: string;
  recording?: number;
};

/** Filters as typed in the box, by name, before they are matched to ids. */
export type TypedFilters = Partial<Record<FilterKey, string>>;

const KEYS: Record<string, FilterKey> = {
  namespace: "namespace",
  ns: "namespace",
  speaker: "speaker",
  emotion: "emotion",
  recording: "recording",
};
const FILTER_RX = /(?:^|\s)(namespace|ns|speaker|emotion|recording):(?:"([^"]*)"?|(\S+))/gi;

/** Split what was typed into the words to search for and the typed filters. */
export function parseQuery(input: string): {
  text: string;
  typed: TypedFilters;
} {
  const typed: TypedFilters = {};
  const text = input
    .replace(FILTER_RX, (_all, key: string, quoted: string | undefined, bare: string | undefined) => {
      const value = (quoted ?? bare ?? "").trim();
      if (value) typed[KEYS[key.toLowerCase()]] = value;
      return " ";
    })
    .replace(/\s+/g, " ")
    .trim();
  return { text, typed };
}

/** `speaker:"Host B"` style text for a filter value (quoted when it has spaces). */
export function filterToken(key: FilterKey, value: string): string {
  return /[\s"]/.test(value) ? `${key}:"${value.replace(/"/g, "")}"` : `${key}:${value}`;
}

/** Emotion labels are stored capitalised (Neutral, Surprise); people type them any way. */
export function normalizeEmotion(value: string): string {
  const v = value.trim().toLowerCase();
  return v ? v[0].toUpperCase() + v.slice(1) : v;
}

/** Words typed with a trailing * (prefix search isn't supported; the backend drops the *). */
export function prefixWords(text: string): string[] {
  return tokens(text)
    .filter((t) => !t.startsWith('"') && /\w\*+$/.test(t))
    .map((t) => t.replace(/\*+$/, ""));
}

/** The "quoted phrases" in a query. */
export function phrases(text: string): string[] {
  return tokens(text)
    .filter((t) => t.length > 2 && t.startsWith('"') && t.endsWith('"'))
    .map((t) => t.slice(1, -1).trim())
    .filter(Boolean);
}

/** Words and phrases, as the backend splits them. */
export function tokens(text: string): string[] {
  return text.match(/"[^"]+"|\S+/g) ?? [];
}

/** True when there is anything to search for (not just OR or punctuation). */
export function hasTerms(text: string): boolean {
  return tokens(text).some((t) => t.toUpperCase() !== "OR" && /[\p{L}\p{N}]/u.test(t));
}

/** Read the search state from URL parameters. */
export function fromParams(p: URLSearchParams): {
  q: string;
  filters: SearchFilters;
} {
  const int = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : undefined);
  return {
    q: p.get("q") ?? "",
    filters: {
      namespace: p.get("ns") || undefined,
      speaker: int(p.get("speaker")),
      emotion: p.get("emotion") || undefined,
      recording: int(p.get("recording")),
    },
  };
}

/** The URL query for a search state (empty values left out). */
export function toParams(q: string, f: SearchFilters): string {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  if (f.namespace) p.set("ns", f.namespace);
  if (f.speaker != null) p.set("speaker", String(f.speaker));
  if (f.emotion) p.set("emotion", f.emotion);
  if (f.recording != null) p.set("recording", String(f.recording));
  return p.toString();
}

export function activeFilterCount(f: SearchFilters): number {
  return [f.namespace, f.speaker, f.emotion, f.recording].filter((v) => v != null && v !== "").length;
}
