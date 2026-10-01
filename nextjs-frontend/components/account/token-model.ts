import { relative, shortDate } from "@/lib/format";

export type TokenExpiry = {
  label: string;
  state: "never" | "ok" | "soon" | "expired";
};

const DAY = 86_400_000;
/** Expiring within this many days is flagged (gold). */
export const SOON_DAYS = 7;

/** "in 90 days" · "in 3 days" (soon) · "expired 4 Apr" · "never". */
export function tokenExpiry(expiresAt: string | null | undefined, now = Date.now()): TokenExpiry {
  if (!expiresAt) return { label: "never", state: "never" };
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return { label: "—", state: "ok" };
  if (t <= now) {
    const d = new Date(t).toLocaleDateString("en-GB", {
      day: "numeric",
      month: "short",
    });
    return { label: `expired ${d}`, state: "expired" };
  }
  return {
    label: relative(expiresAt, now),
    state: t - now <= SOON_DAYS * DAY ? "soon" : "ok",
  };
}

/** When a token created now for `days` days expires ("Expires 29 Dec 2026"); 0 days never expires. */
export function expiresOn(days: number, now = Date.now()): string {
  if (!days) return "Never expires";
  return `Expires ${shortDate(new Date(now + days * DAY).toISOString())}`;
}

export const SCOPE_LABEL: Record<string, string> = {
  read: "Read only",
  write: "Read & write",
};

/** How long keys may last (GET /tokens/limits; admins set them). */
export type TokenLimits = { default_days: number; max_days: number; never_expire: boolean };
export const NO_LIMITS: TokenLimits = { default_days: 90, max_days: 3650, never_expire: true };

/** Validates the "Expires after (days)" field like the backend: whole days up to the most a key may last, and 0
 * (never) only when keys may never expire. */
export function daysError(raw: string, limits: TokenLimits = NO_LIMITS): string | null {
  const never = limits.never_expire;
  const range = `Use whole days from ${never ? 0 : 1} to ${limits.max_days}${never ? " (0 never expires)" : ""}`;
  if (!raw.trim()) return never ? "Enter a number of days (0 never expires)" : "Enter a number of days";
  const n = Number(raw);
  if (!Number.isInteger(n) || n < 0 || n > limits.max_days) return range;
  if (n === 0 && !never) return `Keys have to expire: use 1 to ${limits.max_days} days`;
  return null;
}
