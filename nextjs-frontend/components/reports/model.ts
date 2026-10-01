/**
 * Reports: date ranges, the months on the namespace overview (RP1) and formatting. Pure functions, tested in
 * __tests__/home-reports.test.ts. The numbers themselves come from GET /namespaces/{name}/stats.
 */

export type ReportRange = "6m" | "12m" | "ytd" | "all";

export const RANGE_LABEL: Record<ReportRange, string> = {
  "6m": "Last 6 months",
  "12m": "Last 12 months",
  ytd: "This year",
  all: "All time",
};

export type Bounds = { from: Date | null; to: Date };

/** A day ("2025-12-01", read as a local date) or a timestamp. */
function when(iso: string): Date {
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return day ? new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3])) : new Date(iso);
}

/** The first day of the earliest month in range, and now. "All time" starts at the oldest recording. */
export function rangeBounds(range: ReportRange, now: Date, oldest?: string | null): Bounds {
  const to = now;
  if (range === "all") {
    const t = oldest ? when(oldest) : null;
    return {
      from: t && !Number.isNaN(t.getTime()) ? new Date(t.getFullYear(), t.getMonth(), 1) : null,
      to,
    };
  }
  if (range === "ytd") return { from: new Date(now.getFullYear(), 0, 1), to };
  const months = range === "6m" ? 6 : 12;
  return {
    from: new Date(now.getFullYear(), now.getMonth() - (months - 1), 1),
    to,
  };
}

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Apr – Sep 2026", "Nov 2025 – Apr 2026", "Sep 2026". */
export function rangeLabel(b: Bounds): string {
  const to = b.to;
  if (!b.from) return `Until ${MON[to.getMonth()]} ${to.getFullYear()}`;
  const f = b.from;
  if (f.getFullYear() === to.getFullYear() && f.getMonth() === to.getMonth())
    return `${MON[to.getMonth()]} ${to.getFullYear()}`;
  return f.getFullYear() === to.getFullYear()
    ? `${MON[f.getMonth()]} – ${MON[to.getMonth()]} ${to.getFullYear()}`
    : `${MON[f.getMonth()]} ${f.getFullYear()} – ${MON[to.getMonth()]} ${to.getFullYear()}`;
}

/** The days to ask the stats for: from the first day of the range's first month to today; none for "All time". */
export function rangeQuery(range: ReportRange, now: Date): { from?: string; to?: string } {
  const b = rangeBounds(range, now);
  return range === "all" || !b.from ? {} : { from: isoDay(b.from), to: isoDay(now) };
}

export type MonthBucket = {
  key: string;
  label: string;
  year: number;
  recordings: number;
  ms: number;
};

/** The stats' months ("2026-04", empty ones included) as bars: the latest `max` of them. */
export function monthBars(
  months: { month: string; recordings?: number | null; ms?: number | null }[] | null | undefined,
  max = 24,
): MonthBucket[] {
  return (months ?? []).slice(-max).map((m) => {
    const [year, month] = m.month.split("-").map(Number);
    return { key: m.month, label: MON[month - 1] ?? m.month, year, recordings: m.recordings ?? 0, ms: m.ms ?? 0 };
  });
}

/** Why the totals and the Library's count can differ: recordings without a date. */
export function undatedNote(undated: number | null | undefined, range: ReportRange): string | null {
  if (!undated) return null;
  const n = undated === 1 ? "1 recording has" : `${undated} recordings have`;
  return range === "all"
    ? `${n} no date: counted above, but in no month.`
    : `${n} no date, so ${undated === 1 ? "it isn’t" : "they aren’t"} in any date range.`;
}

/** "51 h", "2 h", "51 m", "40 s": talk time for the speakers list. */
export function talkTime(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} m`;
  const h = ms / 3.6e6;
  return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1).replace(/\.0$/, "")} h`;
}

/** Entity sizes for the "top entities" cloud: 13–22px by mentions, the top third bold. */
export function cloudSizes(
  items: { name: string; mentions: number }[],
): { name: string; mentions: number; size: number; strong: boolean }[] {
  if (!items.length) return [];
  const max = Math.max(...items.map((i) => i.mentions));
  const min = Math.min(...items.map((i) => i.mentions));
  const sorted = [...items].sort((a, b) => b.mentions - a.mentions);
  const strongCut = sorted[Math.max(0, Math.ceil(sorted.length / 3) - 1)].mentions;
  return items.map((i) => ({
    ...i,
    size: max === min ? 16 : Math.round(13 + ((i.mentions - min) / (max - min)) * 9),
    strong: i.mentions >= strongCut && max !== min,
  }));
}

/** yyyy-mm-dd for the entities API's date filters. */
export function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
