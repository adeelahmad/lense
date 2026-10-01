/**
 * Notes on a recording (the Notes tab): yours, and the ones shared with everyone who can read it. Pure functions: the
 * draft "Add note" makes from a transcript selection, the moment a note is about, who wrote it, how the list groups
 * them, and the body the API takes.
 */
import type { Note, NoteCreate } from "@/app/openapi-client/types.gen";
import { tc } from "@/lib/format";

export const TEXT_MAX = 5000;
export const QUOTE_MAX = 1000;

/** What "Add note" in the transcript hands the Notes tab: the moment picked (ms) and its words. */
export type NoteDraft = { t0: number; t1: number; quote: string };

/** The draft for a transcript selection: its moment, and its words on one line (at most QUOTE_MAX characters). */
export function draftFromSelection(t: number, end: number, quote: string): NoteDraft {
  const q = quote.replace(/\s+/g, " ").trim();
  const t0 = Math.max(0, Math.round(t));
  return {
    t0,
    t1: Math.max(t0, Math.round(end)),
    quote: q.length > QUOTE_MAX ? `${q.slice(0, QUOTE_MAX - 1)}…` : q,
  };
}

/** "1:23", or "1:23–1:31" when the moment spans whole seconds; null for a note about the whole recording. A document's
 * notes say where they are with `at` (its page). */
export function momentLabel(n: { t0?: number | null; t1?: number | null }, at?: (ms: number) => string): string | null {
  if (n.t0 == null) return null;
  if (at) return at(n.t0);
  const a = tc(n.t0);
  const b = n.t1 != null ? tc(n.t1) : a;
  return b === a ? a : `${a}–${b}`;
}

/** Who wrote it, as the list says it: "You", their name, or their email. */
export function writer(n: Pick<Note, "mine" | "created_by" | "created_by_name">): string {
  if (n.mine) return "You";
  return n.created_by_name || n.created_by || "Someone";
}

/** Who sees it: "Only you", "Shared" (yours), or who shared it with you. */
export function noteMeta(n: Pick<Note, "mine" | "shared" | "created_by" | "created_by_name">): string {
  if (!n.shared) return "Only you";
  return n.mine ? "You · shared" : `${writer(n)} · shared`;
}

/** The notes about the whole recording, then the ones about a moment, each in the API's order (by moment). */
export function groupNotes(notes: Note[]): { whole: Note[]; moments: Note[] } {
  return {
    whole: notes.filter((n) => n.t0 == null),
    moments: notes.filter((n) => n.t0 != null),
  };
}

/** How many of the notes are yours, and how many others shared. */
export function noteCounts(notes: Note[]): { mine: number; shared: number } {
  return {
    mine: notes.filter((n) => n.mine).length,
    shared: notes.filter((n) => !n.mine).length,
  };
}

/** The question before deleting: a shared note goes for everyone. */
export function deleteQuestion(n: Pick<Note, "mine" | "shared" | "created_by" | "created_by_name">): string {
  if (!n.shared) return "Delete this note?";
  return n.mine ? "Delete it for everyone who can read this recording?" : `Delete ${writer(n)}’s note for everyone?`;
}

/** The body for a new note: its text, its moment (the draft's, or the player's time when pinned to it), sharing. */
export function newNoteBody(
  text: string,
  { draft, at, shared }: { draft: NoteDraft | null; at: number | null; shared: boolean },
): NoteCreate {
  const body: NoteCreate = { text: text.trim(), shared };
  if (draft) return { ...body, t0: draft.t0, t1: draft.t1, ...(draft.quote ? { quote: draft.quote } : {}) };
  if (at != null) return { ...body, t0: Math.max(0, Math.round(at)) };
  return body;
}

/** The notes about a moment that overlaps [t0, t1) (a transcript line's notes). */
export function notesAt(notes: Note[], t0: number, t1: number): Note[] {
  return notes.filter((n) => n.t0 != null && n.t0 < t1 && (n.t1 ?? n.t0) >= t0);
}
