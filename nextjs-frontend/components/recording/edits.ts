/** Transcript corrections: describing an edit in plain words, and the patch that reverts it. Pure functions. */

export type EditRecord = { idx: number; before?: Record<string, unknown> | null; after?: Record<string, unknown> | null; by?: string | null; at?: string | null };

const clip = (s: string, n = 28) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** The words that changed between two versions of a line: common leading and trailing words are dropped. */
export function wordDiff(a: string, b: string): { removed: string; added: string } {
  const x = a.split(/\s+/).filter(Boolean);
  const y = b.split(/\s+/).filter(Boolean);
  let i = 0;
  while (i < x.length && i < y.length && x[i] === y[i]) i++;
  let j = 0;
  while (j < x.length - i && j < y.length - i && x[x.length - 1 - j] === y[y.length - 1 - j]) j++;
  return { removed: x.slice(i, x.length - j).join(" "), added: y.slice(i, y.length - j).join(" ") };
}

/** "Fixed “Meridan” → “Meridian”", "Reassigned line to Host B", … */
export function describeEdit(e: Pick<EditRecord, "before" | "after">, speakerName: (id: number | null) => string): string {
  const after = e.after ?? {};
  const before = e.before ?? {};
  const text = "text" in after;
  const spk = "speaker" in after;
  if (text && spk) return "Edited the text and the speaker";
  if (spk) {
    const id = after.speaker == null ? null : Number(after.speaker);
    return id == null ? "Unassigned the speaker" : `Reassigned line to ${speakerName(id)}`;
  }
  if (text) {
    const { removed, added } = wordDiff(String(before.text ?? ""), String(after.text ?? ""));
    if (removed && added) return `Fixed “${clip(removed)}” → “${clip(added)}”`;
    if (added) return `Added “${clip(added)}”`;
    if (removed) return `Removed “${clip(removed)}”`;
    return "Edited spacing";
  }
  return "Edited a line";
}

/** The PATCH body that puts a line back the way it was before this edit (only the fields the edit changed). */
export function revertPatch(e: Pick<EditRecord, "before" | "after">): { text?: string; speaker?: number | null } | null {
  const after = e.after ?? {};
  const before = e.before ?? {};
  const out: { text?: string; speaker?: number | null } = {};
  if ("text" in after && typeof before.text === "string" && before.text.trim()) out.text = before.text;
  if ("speaker" in after) out.speaker = before.speaker == null ? null : Number(before.speaker);
  return Object.keys(out).length ? out : null;
}
