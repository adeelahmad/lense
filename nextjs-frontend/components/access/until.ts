/**
 * Grants that end (docs/access.md#grants-that-end): a role in a namespace, a role on a collection or permission on an
 * asset can hold until a date. The web app asks for a day ("Until", empty for no end) and the API keeps the moment it
 * ends: the end of that day where the person picking it is. Pure helpers for the date fields and the lines under
 * each person.
 */

/** A grant's end as the API lists it. */
export type GrantEnd = { expires_at?: string | null; expired?: boolean | null };

const pad = (n: number) => String(n).padStart(2, "0");

/** A local day as a date input's value ("2026-11-03"). */
export function dayValue(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The earliest day an "Until" field offers: today (a grant that ends today holds until tonight). */
export function firstDay(now: Date = new Date()): string {
  return dayValue(now);
}

/** What to send as `expires_at` for a day typed in an "Until" field: the end of that day here; null when empty. */
export function untilToIso(day: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day.trim());
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 23, 59, 59).toISOString();
}

/** The day an end falls on here, for an "Until" field ("" for no end). */
export function isoToDay(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : dayValue(d);
}

function shown(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** Whether a grant has ended: the API says so, or its end has come since the list was loaded. */
export function ended(g: GrantEnd, now: Date = new Date()): boolean {
  if (g.expired) return true;
  if (!g.expires_at) return false;
  const t = new Date(g.expires_at).getTime();
  return !Number.isNaN(t) && t <= now.getTime();
}

/** Under a person: "Until 3 Nov 2026", "Ended 1 Oct 2026", or "" for a grant without an end. */
export function untilLine(g: GrantEnd, now: Date = new Date()): string {
  if (!g.expires_at) return "";
  return `${ended(g, now) ? "Ended" : "Until"} ${shown(g.expires_at)}`;
}
