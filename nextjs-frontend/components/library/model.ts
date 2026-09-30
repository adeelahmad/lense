/**
 * The library's view model: status + job overlay, speakers, importance, emotion mix, and the client-side filters and
 * sorting. Pure functions so they can be tested without a browser.
 *
 * The backend lists recordings newest first (`GET /recordings?ns&limit&offset`) and can't filter or sort, so every
 * filter here runs over the rows loaded so far; the page says so when there are more to load.
 */
import type { Job, RecordingSummary } from "@/app/openapi-client/types.gen";
import type { Tone } from "@/components/ui/badge";
import { STEP_LABEL } from "@/components/ui/loop";

/** Recording statuses as the backend names them, in pipeline order. */
export const STATUSES = ["new", "transcribed", "diarized", "analyzed", "error"] as const;
export type RecordingStatus = (typeof STATUSES)[number];

const ACTIVE = new Set(["queued", "running"]);

export function isActiveJob(job: Job | undefined): boolean {
  return Boolean(job && ACTIVE.has(job.status));
}

/** The latest job of each recording. Jobs come newest first, so the first one seen wins. */
export function latestJobs(jobs: Job[] | undefined): Map<number, Job> {
  const out = new Map<number, Job>();
  for (const j of jobs ?? []) {
    if (j.recording != null && !out.has(j.recording)) out.set(j.recording, j);
  }
  return out;
}

export function stepLabel(step: string | null | undefined): string {
  if (!step) return "Processing";
  return STEP_LABEL[step] ?? step.charAt(0).toUpperCase() + step.slice(1);
}

/** Total audio for the header: "12 min", "1.4 h", "96 h". */
export function totalDuration(ms: number | null | undefined): string {
  const min = (ms ?? 0) / 60000;
  if (min < 1) return ms ? "<1 min" : "0 min";
  if (min < 60) return `${Math.round(min)} min`;
  const h = min / 60;
  return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`;
}

/** "<1 min", "4 min", "2 h 5 min": how long something has been going. */
export function elapsed(fromIso: string | null | undefined, now = Date.now()): string | null {
  if (!fromIso) return null;
  const t = Date.parse(fromIso);
  if (Number.isNaN(t)) return null;
  const min = Math.max(0, Math.floor((now - t) / 60000));
  if (min < 1) return "<1 min";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export type SubTone = "muted" | "red" | "gold";

export type StatusView = {
  /** The backend status word, shown on the chip. */
  label: string;
  tone: Tone;
  /** The line under the chip: the running step, "Waiting for a worker", or what failed. */
  sub?: { text: string; tone: SubTone; title?: string };
  /** 0..1 while a job runs (share of its steps done). */
  progress?: number;
  /** What a Retry button next to the sub line should do. */
  retry?: { kind: "job"; job: number } | { kind: "reprocess" };
};

const TONE: Record<string, Tone> = {
  new: "neutral",
  transcribed: "intent",
  diarized: "intent",
  analyzed: "green",
  error: "red",
};

function extra(job: Job, key: string): string | undefined {
  const v = (job as Record<string, unknown>)[key];
  return typeof v === "string" ? v : undefined;
}

function firstLine(s: string | null | undefined, max = 80): string {
  const line = (s ?? "").split("\n")[0].trim();
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** The status chip and the job overlay under it (Library L1: "Transcribe · 4 min", "Waiting for a worker", "… · Retry"). */
export function statusView(
  rec: Pick<RecordingSummary, "status" | "error">,
  job?: Job,
  now = Date.now(),
  reviews = 0,
): StatusView {
  const status = (rec.status || "new").toLowerCase();
  const view: StatusView = { label: status, tone: TONE[status] ?? "neutral" };
  if (job && job.status === "running") {
    const step = stepLabel(job.next_step);
    const took = elapsed(extra(job, "started_at"), now);
    view.sub = { text: took ? `${step} · ${took}` : step, tone: "muted" };
    view.progress = Math.max(0.04, Math.min(1, job.progress ?? 0));
    return view;
  }
  if (job && job.status === "queued") {
    const handedOff = (job.step_index ?? 0) > 0;
    view.sub = {
      text: handedOff ? `${stepLabel(job.next_step)} · waiting for a worker` : "Waiting for a worker",
      tone: "muted",
    };
    return view;
  }
  if (job && job.status === "failed") {
    view.sub = {
      text: `${stepLabel(job.next_step)} failed`,
      tone: "red",
      title: firstLine(job.error, 300) || undefined,
    };
    view.retry = { kind: "job", job: job.id };
    return view;
  }
  if (status === "error") {
    view.sub = {
      text: firstLine(rec.error) || "Processing failed",
      tone: "red",
      title: firstLine(rec.error, 300) || undefined,
    };
    view.retry = { kind: "reprocess" };
    return view;
  }
  if (reviews > 0)
    view.sub = {
      text: `◆ ${reviews} voice ${reviews === 1 ? "match" : "matches"} to review`,
      tone: "gold",
    };
  else if (job && job.status === "cancelled") view.sub = { text: "Cancelled", tone: "muted" };
  return view;
}

// ---------- speakers ----------

const UNNAMED = /^(speaker[\s_-]*\d+|spk[\s_-]*\d+|unknown|\?)$/i;

/** Labels the diarizer made up ("Speaker 2", "SPEAKER_00") get a dashed ring until someone names them. */
export function isUnnamedSpeaker(name: string): boolean {
  return UNNAMED.test(name.trim());
}

export type SpeakerRef = { name: string; unnamed: boolean; index: number };

/** The list's comma-separated speaker names, in order of first appearance. */
export function speakerList(speakers: string | null | undefined): SpeakerRef[] {
  const names = (speakers ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s && s !== "?");
  return [...new Set(names)].map((name, index) => ({
    name,
    unnamed: isUnnamedSpeaker(name),
    index,
  }));
}

// ---------- importance and tone ----------

export type Importance = {
  bars: 1 | 2 | 3;
  label: "Low" | "Medium" | "High";
  value: number;
};

/** The summary's importance (1 routine … 5 critical) as the design's three bars: Low · Medium · High. */
export function importanceInfo(v: unknown): Importance | null {
  let n: number | null = null;
  if (typeof v === "number" && Number.isFinite(v)) n = v;
  else if (typeof v === "string") {
    const s = v.trim().toLowerCase();
    n = s === "high" ? 5 : s === "medium" ? 3 : s === "low" ? 1 : Number.parseFloat(s);
  }
  if (n == null || Number.isNaN(n)) return null;
  const value = Math.max(1, Math.min(5, Math.round(n)));
  if (value >= 4) return { bars: 3, label: "High", value };
  if (value === 3) return { bars: 2, label: "Medium", value };
  return { bars: 1, label: "Low", value };
}

export function toneWord(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim().toLowerCase() : null;
}

/** Emotion → milliseconds, without "Unknown" (lines the analyser couldn't read). */
export function emotionMix(emotions: Record<string, unknown> | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(emotions ?? {})) {
    if (k === "Unknown" || typeof v !== "number" || v <= 0) continue;
    out[k] = v;
  }
  return out;
}

// ---------- filters ----------

export type DateRange = "any" | "today" | "7d" | "30d" | "90d" | "1y";
export type DurationRange = "any" | "short" | "medium" | "long" | "xlong";
export type MediaFilter = "any" | "audio" | "video" | "transcript";
export type LibraryView = "all" | "attention" | "processing";
/** Status filter values: the backend statuses plus two job states. */
export type StatusFilter = RecordingStatus | "processing" | "failed";

export type Filters = {
  q: string;
  statuses: StatusFilter[];
  speaker: string | null;
  date: DateRange;
  duration: DurationRange;
  media: MediaFilter;
};

export const NO_FILTERS: Filters = {
  q: "",
  statuses: [],
  speaker: null,
  date: "any",
  duration: "any",
  media: "any",
};

export const DATE_LABEL: Record<DateRange, string> = {
  any: "Any time",
  today: "Today",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  "1y": "Last 12 months",
};

export const DURATION_LABEL: Record<DurationRange, string> = {
  any: "Any length",
  short: "Under 10 min",
  medium: "10–30 min",
  long: "30–60 min",
  xlong: "Over 1 hour",
};

export const MEDIA_LABEL: Record<MediaFilter, string> = {
  any: "Any",
  audio: "Audio",
  video: "Video",
  transcript: "Transcript only",
};

export const STATUS_FILTER_LABEL: Record<StatusFilter, string> = {
  new: "New",
  transcribed: "Transcribed",
  diarized: "Diarized",
  analyzed: "Analyzed",
  error: "Error",
  processing: "Processing now",
  failed: "Job failed",
};

export function activeFilterCount(f: Filters): number {
  return (
    (f.q.trim() ? 1 : 0) +
    (f.statuses.length ? 1 : 0) +
    (f.speaker ? 1 : 0) +
    (f.date !== "any" ? 1 : 0) +
    (f.duration !== "any" ? 1 : 0) +
    (f.media !== "any" ? 1 : 0)
  );
}

const DAY = 86_400_000;

function inDate(iso: string | null | undefined, range: DateRange, now: number): boolean {
  if (range === "any") return true;
  const t = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(t)) return false;
  if (range === "today") return new Date(t).toDateString() === new Date(now).toDateString();
  const days = { "7d": 7, "30d": 30, "90d": 90, "1y": 365 }[range];
  return now - t <= days * DAY;
}

function inDuration(ms: number | null | undefined, range: DurationRange): boolean {
  if (range === "any") return true;
  if (ms == null) return false;
  const min = ms / 60000;
  if (range === "short") return min < 10;
  if (range === "medium") return min >= 10 && min < 30;
  if (range === "long") return min >= 30 && min < 60;
  return min >= 60;
}

/** Whether a row needs someone: it errored, its latest job failed, or voice matches wait for review. */
export function needsAttention(rec: RecordingSummary, job?: Job, reviews = 0): boolean {
  return (rec.status || "").toLowerCase() === "error" || job?.status === "failed" || reviews > 0;
}

export function matchesFilters(rec: RecordingSummary, f: Filters, job: Job | undefined, now = Date.now()): boolean {
  const q = f.q.trim().toLowerCase();
  if (q) {
    const hay = `${rec.title ?? ""} ${rec.namespace ?? ""} ${rec.speakers ?? ""}`.toLowerCase();
    if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  if (f.statuses.length) {
    const st = (rec.status || "new").toLowerCase();
    const ok = f.statuses.some((s) =>
      s === "processing" ? isActiveJob(job) : s === "failed" ? job?.status === "failed" : s === st,
    );
    if (!ok) return false;
  }
  if (f.speaker && !speakerList(rec.speakers).some((s) => s.name === f.speaker)) return false;
  if (!inDate(rec.recorded_at, f.date, now)) return false;
  if (!inDuration(rec.duration_ms, f.duration)) return false;
  if (f.media !== "any" && (rec.media_kind || "transcript") !== f.media) return false;
  return true;
}

export function matchesView(rec: RecordingSummary, view: LibraryView, job: Job | undefined, reviews = 0): boolean {
  if (view === "attention") return needsAttention(rec, job, reviews);
  if (view === "processing") return isActiveJob(job);
  return true;
}

// ---------- sorting ----------

export type SortKey = "title" | "date" | "duration" | "speakers" | "status" | "importance";
export type SortDir = "asc" | "desc";

const STATUS_ORDER: Record<string, number> = {
  error: 0,
  new: 1,
  transcribed: 2,
  diarized: 3,
  analyzed: 4,
};

function sortValue(rec: RecordingSummary, key: SortKey): string | number | null {
  switch (key) {
    case "title":
      return (rec.title ?? "").toLowerCase();
    case "date":
      return rec.recorded_at ? Date.parse(rec.recorded_at) : null;
    case "duration":
      return rec.duration_ms ?? null;
    case "speakers":
      return speakerList(rec.speakers).length;
    case "status":
      return STATUS_ORDER[(rec.status || "new").toLowerCase()] ?? 5;
    case "importance":
      return importanceInfo(rec.importance)?.value ?? null;
  }
}

/** A stable sort; rows without a value go last either way. */
export function sortRows<T extends RecordingSummary>(rows: T[], key: SortKey, dir: SortDir): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((r, i) => ({ r, i, v: sortValue(r, key) }))
    .sort((a, b) => {
      if (a.v == null && b.v == null) return a.i - b.i;
      if (a.v == null) return 1;
      if (b.v == null) return -1;
      const c = typeof a.v === "string" ? a.v.localeCompare(b.v as string) : (a.v as number) - (b.v as number);
      return c ? c * sign : a.i - b.i;
    })
    .map((x) => x.r);
}

/** Rows from index a to b inclusive, either direction: shift-click range selection. */
export function rangeIds(ids: number[], from: number, to: number): number[] {
  const [a, b] = from < to ? [from, to] : [to, from];
  return ids.slice(Math.max(0, a), b + 1);
}
