/**
 * Who may see a recording (docs/access.md, after Aviary's roles and permissions): public, restricted (listed with a
 * lock for signed-in people) or private, the parts a public recording opens to everyone, and whether it's featured.
 * Pure helpers shared by the Access dialog, the IIIF panel, the metadata editor and the Library.
 */

export type Access = "public" | "restricted" | "private";
/** The parts of a public recording anyone may use. */
export type AccessPart = "media" | "transcript" | "index";
export type AccessValue = { access: Access; open: AccessPart[]; featured: boolean };

export const ACCESS: {
  value: Access;
  label: string;
  hint: string;
  anon: string;
}[] = [
  {
    value: "public",
    label: "Public",
    hint: "anyone",
    anon: "Anyone finds it and opens its page and description; the parts left open are theirs to use. Published in IIIF.",
  },
  {
    value: "restricted",
    label: "Restricted",
    hint: "listed with a lock",
    anon: "Signed-in people see it listed with a lock and can ask for access; visitors don’t see it. Not published in IIIF.",
  },
  {
    value: "private",
    label: "Private",
    hint: "people with access",
    anon: "Only people with access see it: this namespace’s members, anyone it’s shared with and its IP groups. Not published in IIIF.",
  },
];

export const PARTS: { value: AccessPart; label: string; hint: string }[] = [
  { value: "media", label: "Media", hint: "audio or video" },
  { value: "transcript", label: "Transcript", hint: "the text, its search and downloads" },
  { value: "index", label: "Index", hint: "chapters" },
];

export const ALL_PARTS: AccessPart[] = PARTS.map((p) => p.value);

export function accessLabel(a: string | null | undefined): string {
  return ACCESS.find((x) => x.value === a)?.label ?? "Private";
}

/** "Media, transcript and index", "Transcript", "Nothing": the parts a public recording opens. */
export function partsText(open: readonly string[] | null | undefined): string {
  const names = PARTS.filter((p) => (open ?? []).includes(p.value)).map((p) => p.label.toLowerCase());
  if (!names.length) return "Nothing";
  const text = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
  return text[0].toUpperCase() + text.slice(1);
}

/** One line for a chip's tooltip: "Public · transcript and index open to everyone · featured". */
export function accessSummary(a: Partial<AccessValue> | null | undefined): string {
  const level = (a?.access ?? "private") as Access;
  const parts: string[] = [accessLabel(level)];
  if (level === "public") {
    const open = a?.open ?? ALL_PARTS;
    parts.push(
      open.length === ALL_PARTS.length
        ? "everything open"
        : open.length
          ? `${partsText(open).toLowerCase()} open to everyone`
          : "page and description only",
    );
    if (a?.featured) parts.push("featured");
  }
  return parts.join(" · ");
}

/** Toggle one part in or out, keeping the canonical order (media, transcript, index). */
export function togglePart(open: readonly AccessPart[], part: AccessPart, on: boolean): AccessPart[] {
  const set = new Set(open);
  if (on) set.add(part);
  else set.delete(part);
  return ALL_PARTS.filter((p) => set.has(p));
}

/** What to send to PUT /recordings/{id}/access to go from `saved` to `draft` (only what changed). */
export function accessPatch(
  saved: AccessValue,
  draft: AccessValue,
): Partial<{ access: Access; open: AccessPart[]; featured: boolean }> {
  const out: Partial<{ access: Access; open: AccessPart[]; featured: boolean }> = {};
  if (draft.access !== saved.access) out.access = draft.access;
  if (draft.open.join() !== saved.open.join()) out.open = draft.open;
  if (draft.featured !== saved.featured) out.featured = draft.featured;
  return out;
}

/** Under a person with permission: who gave it and when ("Given by ana@example.org on 30 Sep 2026"). */
export function permissionLine(p: { by?: string | null; at?: string | null }): string {
  const d = p.at ? new Date(p.at) : null;
  const when =
    d && !Number.isNaN(d.getTime())
      ? d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" })
      : "";
  return ["Given", p.by ? `by ${p.by}` : "", when ? `on ${when}` : ""].filter(Boolean).join(" ");
}

/** IP groups (docs/access.md): addresses typed one per line (or separated by commas or spaces), without repeats. */
export function rangesFromText(text: string): string[] {
  const out: string[] = [];
  for (const r of text.split(/[\s,;]+/)) if (r && !out.includes(r)) out.push(r);
  return out;
}

/** A group's ranges in one line: "198.51.100.0/24, 2001:db8::/48 and 3 more". */
export function rangesText(ranges: readonly string[], max = 2): string {
  if (ranges.length <= max + 1) return ranges.join(", ");
  return `${ranges.slice(0, max).join(", ")} and ${ranges.length - max} more`;
}

/** What an IP group opens: "Every recording in pods", "3 chosen recordings", or how to choose some. */
export function ipGroupOpens(g: { everything: boolean; chosen?: number | null }, ns: string): string {
  if (g.everything) return `Every recording in ${ns}`;
  const n = g.chosen ?? 0;
  if (!n) return "No recordings yet: choose them in a recording’s Access dialog";
  return `${n} chosen recording${n === 1 ? "" : "s"}`;
}
