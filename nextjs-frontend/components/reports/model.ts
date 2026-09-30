/**
 * Reports: date ranges, monthly buckets and the numbers on the namespace overview (RP1). Pure functions, tested in
 * __tests__/reports-model.test.ts. The backend has no report aggregates, so these are computed from the recordings list.
 */
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { speakerList } from "@/components/library/model";

export type ReportRange = "6m" | "12m" | "ytd" | "all";

export const RANGE_LABEL: Record<ReportRange, string> = {
  "6m": "Last 6 months",
  "12m": "Last 12 months",
  ytd: "This year",
  all: "All time",
};

export type Bounds = { from: Date | null; to: Date };

/** The first day of the earliest month in range, and now. "All time" starts at the oldest recording. */
export function rangeBounds(range: ReportRange, now: Date, oldest?: string | null): Bounds {
  const to = now;
  if (range === "all") {
    const t = oldest ? new Date(oldest) : null;
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

export function inBounds(iso: string | null | undefined, b: Bounds): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return false;
  return (!b.from || t >= b.from.getTime()) && t <= b.to.getTime() + 60_000;
}

export type MonthBucket = {
  key: string;
  label: string;
  year: number;
  recordings: number;
  ms: number;
};

/** One bucket per calendar month from `from` to `to` (inclusive), empty months included. */
export function monthlyBuckets(recs: RecordingSummary[], b: Bounds, maxMonths = 24): MonthBucket[] {
  const start =
    b.from ??
    (() => {
      const ts = recs.map((r) => Date.parse(r.recorded_at ?? "")).filter((t) => !Number.isNaN(t));
      const t = ts.length ? new Date(Math.min(...ts)) : b.to;
      return new Date(t.getFullYear(), t.getMonth(), 1);
    })();
  const out: MonthBucket[] = [];
  const cur = new Date(start.getFullYear(), start.getMonth(), 1);
  while (cur <= b.to) {
    out.push({
      key: `${cur.getFullYear()}-${String(cur.getMonth() + 1).padStart(2, "0")}`,
      label: MON[cur.getMonth()],
      year: cur.getFullYear(),
      recordings: 0,
      ms: 0,
    });
    cur.setMonth(cur.getMonth() + 1);
  }
  const trimmed = out.slice(-maxMonths);
  const idx = new Map(trimmed.map((m, i) => [m.key, i]));
  for (const r of recs) {
    if (!inBounds(r.recorded_at, b)) continue;
    const d = new Date(r.recorded_at as string);
    const i = idx.get(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    if (i == null) continue;
    trimmed[i].recordings += 1;
    trimmed[i].ms += r.duration_ms ?? 0;
  }
  return trimmed;
}

export type Overview = { recordings: number; ms: number; speakers: number };

/** Headline numbers for the recordings in range; speakers are the distinct names that appear in them. */
export function overview(recs: RecordingSummary[], b: Bounds): Overview {
  const names = new Set<string>();
  let n = 0;
  let ms = 0;
  for (const r of recs) {
    if (!inBounds(r.recorded_at, b)) continue;
    n++;
    ms += r.duration_ms ?? 0;
    for (const s of speakerList(r.speakers)) names.add(s.name);
  }
  return { recordings: n, ms, speakers: names.size };
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
