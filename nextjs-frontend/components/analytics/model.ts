import type { ActionCounts, ActivityEntry } from "@/app/openapi-client/types.gen";

/** The actions Lens counts (docs/analytics.md), in the order they're shown. */
export const ACTIONS = ["view", "play", "search", "download", "comment"] as const;
export type Action = (typeof ACTIONS)[number];

export const ACTION_LABEL: Record<Action, { one: string; many: string; did: string }> = {
  view: { one: "view", many: "Views", did: "Opened" },
  play: { one: "play", many: "Plays", did: "Played" },
  search: { one: "search", many: "Searches", did: "Searched" },
  download: { one: "download", many: "Downloads", did: "Downloaded from" },
  comment: { one: "comment", many: "Comments", did: "Commented on" },
};

/** The ranges offered: how many days back from today. */
export const RANGES = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
];

export function rangeDays(value: string | null | undefined): number {
  return RANGES.some((r) => r.value === value) ? Number(value) : 30;
}

/** Everything that was done: the sum over the actions. */
export function totalOf(c: Partial<ActionCounts>): number {
  return ACTIONS.reduce((n, a) => n + (c[a] ?? 0), 0);
}

/** "3 Oct" for a day of the range (days are UTC). */
export function dayLabel(day: string): string {
  const d = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return day;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** Which days get a label under the chart: the first, the last and about four in between. */
export function tickDays(days: { day: string }[], most = 6): Set<string> {
  if (days.length <= most) return new Set(days.map((d) => d.day));
  const step = (days.length - 1) / (most - 1);
  return new Set(Array.from({ length: most }, (_, i) => days[Math.round(i * step)].day));
}

/** One line of someone's own activity: "Opened ‘At sea’", "Searched podcasts", "Searched". */
export function activityLine(e: ActivityEntry): { did: string; what: string | null } {
  const did = ACTION_LABEL[e.action as Action]?.did ?? e.action;
  if (e.action === "search") return { did, what: e.namespace ?? null };
  if (e.resource == null) return { did, what: null };
  return { did, what: e.title ?? "a resource you can no longer open" };
}

/** Who may see a namespace's analytics, said to someone who can't. */
export function whyNot(opts: {
  namespace: string | null;
  admin: boolean;
  owner: boolean;
  partial: boolean;
}): string | null {
  if (opts.admin) return null;
  if (!opts.namespace) return "Pick a namespace you own in the top bar: the whole archive’s analytics are for admins.";
  if (opts.owner) return null;
  if (opts.partial)
    return `Pick a collection you’re an admin of: ${opts.namespace}’s analytics as a whole are for its owners.`;
  return `Owners of ${opts.namespace} can see its analytics.`;
}
