import type { Passage } from "@/app/openapi-client/types.gen";
import { recordingHref } from "@/components/search/links";
import { tc } from "@/lib/format";

/** Answer text split around its [n] citations ([1], [1, 2] and [1][2] all work). */
export type TextPart = { type: "text"; text: string } | { type: "cite"; n: number };

export function splitCitations(text: string): TextPart[] {
  const out: TextPart[] = [];
  const re = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ type: "text", text: text.slice(last, m.index) });
    for (const n of m[1].split(",")) out.push({ type: "cite", n: Number(n.trim()) });
    last = re.lastIndex;
  }
  if (last < text.length) out.push({ type: "text", text: text.slice(last) });
  return out;
}

/** The citation numbers used in a text, in order of first use. */
export function citedNumbers(text: string): number[] {
  const seen: number[] = [];
  for (const p of splitCitations(text)) if (p.type === "cite" && !seen.includes(p.n)) seen.push(p.n);
  return seen;
}

/**
 * A short name for a recording in a citation chip: "Episode 12 — Reading a model system card" → "Ep. 12",
 * "Renewal call — Northwind Labs, Feb" → "Renewal call".
 */
export function shortTitle(title: string | null | undefined, max = 24): string {
  let t = (title ?? "").trim();
  if (!t) return "Recording";
  const cut = t.split(/\s+[—–]\s+|\s+-\s+|:\s+/)[0].trim();
  if (cut) t = cut;
  t = t.replace(/^episode\s+/i, "Ep. ").replace(/^interview\s+/i, "Interview ");
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
}

/** When a passage starts, as "14:29" (tools send `time`; retrieval sends both). */
export function passageTime(p: Pick<Passage, "t0" | "time">): string {
  if (p.time) return p.time;
  return tc(typeof p.t0 === "number" ? p.t0 : 0);
}

/** "Ep. 12 · 14:29 · Host B": recording · time · speaker. */
export function citeLabel(p: Pick<Passage, "title" | "t0" | "time" | "speaker">, withSpeaker = true): string {
  return [shortTitle(p.title), passageTime(p), withSpeaker ? p.speaker : null].filter(Boolean).join(" · ");
}

export type QuoteLine = { speaker: string | null; text: string };

/** A passage's text as lines. Retrieval joins neighbouring lines as "Name: text"; tool results are bare text. */
export function passageLines(p: Pick<Passage, "text" | "speaker">): QuoteLine[] {
  const lines = (p.text ?? "").split(/\n+/).filter((l) => l.trim());
  return lines.map((l) => {
    const m = /^([^:\n]{1,48}):\s+(.*)$/.exec(l);
    if (m && !/^on screen$/i.test(m[1])) return { speaker: m[1].trim(), text: m[2] };
    return {
      speaker: lines.length === 1 ? (p.speaker ?? null) : null,
      text: l.replace(/^On screen:\s*/i, ""),
    };
  });
}

/** The line a citation points at: the one said by the passage's speaker, else the first. */
export function quoteOf(p: Pick<Passage, "text" | "speaker">): QuoteLine {
  const lines = passageLines(p);
  return (
    lines.find((l) => p.speaker && l.speaker === p.speaker) ?? lines[0] ?? { speaker: p.speaker ?? null, text: "" }
  );
}

/** Link to the cited moment. */
export function passageHref(p: Pick<Passage, "recording_id" | "t0">): string {
  return recordingHref(p.recording_id, typeof p.t0 === "number" ? p.t0 : 0);
}

/** The backend's canned answers when no language model is configured. */
export const NO_MODEL_PREFIX = "No language model is configured";
export const NOTHING_MATCHES = "Nothing you can access in the archive matches that.";
export const NO_ANSWER = "(no answer)";

export function isNoModelAnswer(text: string | null | undefined): boolean {
  return (text ?? "").startsWith(NO_MODEL_PREFIX);
}

/** Split into sentences the way the backend's source check does. */
export function sentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Whitespace- and case-insensitive form of a claim, for matching verdicts back to sentences. */
export function normClaim(s: string): string {
  return s.replace(/\s+/g, " ").trim().toLowerCase();
}
