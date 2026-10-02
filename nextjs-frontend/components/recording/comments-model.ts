/**
 * Comments on a resource (the Comments tab) and highlights (passages its editors mark in colour). Pure functions:
 * how comments thread and which threads are open, the body the API takes, where comments and highlights fall in the
 * text, and the colours highlights come in (each a token in app/styles/tokens.css).
 */
import type { Comment, CommentCreate, Highlight, HighlightCreate } from "@/app/openapi-client/types.gen";
import type { Segment } from "@/components/recording/model";
import { writer, type NoteDraft } from "@/components/recording/notes-model";

export const TEXT_MAX = 5000;
export const LABEL_MAX = 200;

export type Thread = { root: Comment; replies: Comment[] };

/** The threads, in the API's order (about the whole resource first, then by moment), each with its replies. */
export function threads(comments: Comment[]): Thread[] {
  const out: Thread[] = [];
  const by = new Map<number, Thread>();
  for (const c of comments) {
    if (c.parent == null) {
      const t = { root: c, replies: [] };
      out.push(t);
      by.set(c.id, t);
    }
  }
  for (const c of comments) {
    if (c.parent != null) by.get(c.parent)?.replies.push(c);
  }
  return out;
}

/** How many threads aren't resolved. */
export function openThreads(comments: Comment[]): number {
  return comments.filter((c) => c.parent == null && !c.resolved).length;
}

/** The body for a new comment: its text, and the thread it replies to, or its moment (the draft's, or the clock's
 * when pinned to it). */
export function newCommentBody(
  text: string,
  { draft, at, parent }: { draft: NoteDraft | null; at: number | null; parent?: number | null },
): CommentCreate {
  const body: CommentCreate = { text: text.trim() };
  if (parent != null) return { ...body, parent };
  if (draft) return { ...body, t0: draft.t0, t1: draft.t1, ...(draft.quote ? { quote: draft.quote } : {}) };
  if (at != null) return { ...body, t0: Math.max(0, Math.round(at)) };
  return body;
}

/** The threads about a moment that overlaps [t0, t1) (a transcript line's). */
export function commentsAt(comments: Comment[], t0: number, t1: number): Comment[] {
  return comments.filter((c) => c.parent == null && c.t0 != null && c.t0 < t1 && (c.t1 ?? c.t0) >= t0);
}

/** "Resolved by Ana" (their name, or their email). */
export function resolvedBy(c: Pick<Comment, "resolved" | "resolved_by" | "resolved_by_name">): string | null {
  if (!c.resolved) return null;
  return `Resolved by ${c.resolved_by_name || c.resolved_by || "someone"}`;
}

/** The question before deleting: a thread goes with its replies, for everyone. */
export function deleteCommentQuestion(
  c: Pick<Comment, "mine" | "parent" | "created_by" | "created_by_name">,
  replies: number,
): string {
  const whose = c.mine ? "your" : `${writer(c)}’s`;
  if (c.parent != null) return `Delete ${whose} reply for everyone?`;
  if (!replies) return `Delete ${whose} comment for everyone?`;
  return `Delete ${whose} comment and its ${replies === 1 ? "reply" : `${replies} replies`} for everyone?`;
}

export type HighlightColour = Highlight["colour"];

/** The colours a highlight comes in: the mark on the text, and the swatch that picks it. Tokens only. */
export const COLOURS: { value: HighlightColour; label: string; mark: string; swatch: string }[] = [
  {
    value: "yellow",
    label: "Yellow",
    mark: "bg-gold-surface shadow-[0_0_0_1px_var(--gate-border)]",
    swatch: "bg-gold",
  },
  {
    value: "green",
    label: "Green",
    mark: "bg-green-surface shadow-[0_0_0_1px_var(--green-border)]",
    swatch: "bg-green",
  },
  { value: "blue", label: "Blue", mark: "bg-blue-surface shadow-[0_0_0_1px_var(--intent-border)]", swatch: "bg-blue" },
  { value: "red", label: "Red", mark: "bg-red-surface shadow-[0_0_0_1px_var(--red-border)]", swatch: "bg-red" },
];

export function colourOf(colour: string): (typeof COLOURS)[number] {
  return COLOURS.find((c) => c.value === colour) ?? COLOURS[0];
}

/** The body for a new highlight of a transcript selection, in a colour. */
export function newHighlightBody(draft: NoteDraft, colour: HighlightColour = "yellow"): HighlightCreate {
  return { t0: draft.t0, t1: draft.t1, colour, ...(draft.quote ? { quote: draft.quote } : {}) };
}

/** What the list calls a highlight: its label, else its words, else its colour. */
export function highlightTitle(h: Pick<Highlight, "label" | "quote" | "colour">): string {
  if (h.label) return h.label;
  if (h.quote) return h.quote.length > 80 ? `${h.quote.slice(0, 79)}…` : h.quote;
  return `${colourOf(h.colour).label} highlight`;
}

export type HighlightRange = { start: number; end: number; id: number; colour: HighlightColour; label: string | null };

/**
 * Where the highlights fall in a segment's text: the characters its passage covers, by the segment's timed words
 * when it has them, else in proportion to its time (as a selection's time is read from its place in the text).
 */
export function highlightRanges(seg: Segment, highlights: Highlight[]): HighlightRange[] {
  const out: HighlightRange[] = [];
  const len = seg.text.length;
  if (!len) return out;
  for (const h of highlights) {
    if (!(h.t0 < seg.t1 && h.t1 >= seg.t0)) continue;
    let start = 0;
    let end = len;
    if (!(h.t0 <= seg.t0 && h.t1 >= seg.t1)) {
      if (seg.words?.length) {
        const inside = seg.words.filter((w) => w[3] > h.t0 && w[2] < h.t1);
        if (inside.length) {
          start = inside[0][0];
          end = inside[inside.length - 1][1];
        }
      } else if (seg.t1 > seg.t0) {
        const span = seg.t1 - seg.t0;
        start = Math.max(0, Math.round((len * (h.t0 - seg.t0)) / span));
        end = Math.min(len, Math.round((len * (h.t1 - seg.t0)) / span));
      }
    }
    if (end > start) out.push({ start, end, id: h.id, colour: h.colour, label: h.label ?? null });
  }
  return out;
}

export type Run = { text: string; kind: string | null; hl: HighlightRange | null };

/**
 * A segment's text as runs: under each, the first of the inner marks (find hits, entity names) that covers it, and
 * the first highlight. Pieces alike are joined, so the text splits only where a mark starts or ends.
 */
export function highlightRuns(
  text: string,
  inner: { start: number; end: number; kind: string }[],
  hls: HighlightRange[],
): Run[] {
  const clamp = (n: number) => Math.max(0, Math.min(text.length, n));
  const cuts = new Set<number>([0, text.length]);
  for (const r of [...inner, ...hls]) {
    cuts.add(clamp(r.start));
    cuts.add(clamp(r.end));
  }
  const at = [...cuts].sort((a, b) => a - b);
  const out: Run[] = [];
  for (let i = 0; i + 1 < at.length; i++) {
    const [a, b] = [at[i], at[i + 1]];
    if (b <= a) continue;
    const kind = inner.find((r) => r.start <= a && r.end >= b)?.kind ?? null;
    const hl = hls.find((r) => r.start <= a && r.end >= b) ?? null;
    const last = out[out.length - 1];
    if (last && last.kind === kind && last.hl === hl) last.text += text.slice(a, b);
    else out.push({ text: text.slice(a, b), kind, hl });
  }
  return out.length ? out : [{ text, kind: null, hl: null }];
}

/** Why a comment was flagged, as owners read it: "spam (93% sure), personal details (75% sure)". */
export function flagSummary(reasons: { label: string; p: number }[]): string {
  return reasons.map((r) => `${r.label.toLowerCase()} (${Math.round(r.p * 100)}% sure)`).join(", ");
}
