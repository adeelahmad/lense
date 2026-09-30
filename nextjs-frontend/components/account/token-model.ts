import { relative, shortDate } from "@/lib/format";

export type TokenExpiry = { label: string; state: "never" | "ok" | "soon" | "expired" };

const DAY = 86_400_000;
/** Expiring within this many days is flagged (gold). */
export const SOON_DAYS = 7;

/** "in 90 days" · "in 3 days" (soon) · "expired 4 Apr" · "never". */
export function tokenExpiry(expiresAt: string | null | undefined, now = Date.now()): TokenExpiry {
  if (!expiresAt) return { label: "never", state: "never" };
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return { label: "—", state: "ok" };
  if (t <= now) {
    const d = new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
    return { label: `expired ${d}`, state: "expired" };
  }
  return { label: relative(expiresAt, now), state: t - now <= SOON_DAYS * DAY ? "soon" : "ok" };
}

/** When a token created now for `days` days expires ("Expires 29 Dec 2026"); 0 days never expires. */
export function expiresOn(days: number, now = Date.now()): string {
  if (!days) return "Never expires";
  return `Expires ${shortDate(new Date(now + days * DAY).toISOString())}`;
}

export const SCOPE_LABEL: Record<string, string> = { read: "Read only", write: "Read & write" };

/** Validates the "Expires after (days)" field like the backend (0–3650, whole days). */
export function daysError(raw: string): string | null {
  if (!raw.trim()) return "Enter a number of days (0 never expires)";
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 0 || n > 3650) return "Use whole days from 0 to 3650 (0 never expires)";
  return null;
}
