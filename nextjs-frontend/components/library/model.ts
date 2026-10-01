/**
 * The library's view model: status + job overlay, speakers, importance, emotion mix, and the list query that the
 * filters, tabs and sort turn into. Pure functions so they can be tested without a browser.
 *
 * Filtering, sorting and counting happen on the server (`GET /recordings`), over every recording in scope; the list
 * endpoint says how many match in its X-Total-Count header.
 */
import type { Job, ListRecordingsData, RecordingSummary, Speaker } from "@/app/openapi-client/types.gen";
import type { Tone } from "@/components/ui/badge";
import { STEP_LABEL } from "@/components/ui/loop";
import { plural } from "@/lib/format";

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

// ---------- filters (answered by the server) ----------

export type DateRange = "any" | "today" | "7d" | "30d" | "90d" | "1y";
export type DurationRange = "any" | "short" | "medium" | "long" | "xlong";
export type MediaFilter = "any" | "audio" | "video" | "transcript";
export type LibraryView = "all" | "attention" | "processing" | "mine";
/** Status filter values: the backend statuses plus two job states. */
export type StatusFilter = RecordingStatus | "processing" | "failed";
/** A speaker picked by name. Speakers belong to one namespace, so one name can stand for an id in each of several. */
export type SpeakerFilter = { name: string; ids: number[] };

export type Filters = {
  q: string;
  statuses: StatusFilter[];
  speaker: SpeakerFilter | null;
  date: DateRange;
  duration: DurationRange;
  media: MediaFilter;
  /** Any of these tags. */
  tags: string[];
  /** Where they came from (GET /recordings/origins): source:<id>, upload, paste, iiif, folder or file. */
  origins: string[];
  /** Language codes; "none" for recordings whose language isn't known. */
  languages: string[];
  /** A collection of the namespace shown: the recordings in it and in the collections inside it. */
  collection: number | null;
  /** A custom field of the namespace shown: the recordings with a value for it, or with this value. */
  field: FieldFilter | null;
};

/** A custom field filter: its id, and the value to match (empty: any value). */
export type FieldFilter = { id: number; value: string };

export const NO_FILTERS: Filters = {
  q: "",
  statuses: [],
  speaker: null,
  date: "any",
  duration: "any",
  media: "any",
  tags: [],
  origins: [],
  languages: [],
  collection: null,
  field: null,
};

/** "en" → "English", "pt-BR" → "Brazilian Portuguese"; null (not known) → "Not known"; an odd code stays as it is. */
export function languageName(code: string | null | undefined): string {
  if (!code || code === "none") return "Not known";
  try {
    const name = new Intl.DisplayNames(["en"], { type: "language" }).of(code);
    return name && name.toLowerCase() !== code.toLowerCase() ? name : code;
  } catch {
    return code;
  }
}

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
    (f.media !== "any" ? 1 : 0) +
    (f.tags.length ? 1 : 0) +
    (f.origins.length ? 1 : 0) +
    (f.languages.length ? 1 : 0) +
    (f.collection != null ? 1 : 0) +
    (f.field ? 1 : 0)
  );
}

const DAY = 86_400_000;

/** Seconds [at least, under] for each duration range. */
const DURATION_SECONDS: Record<Exclude<DurationRange, "any">, [number | null, number | null]> = {
  short: [null, 600],
  medium: [600, 1800],
  long: [1800, 3600],
  xlong: [3600, null],
};

/** YYYY-MM-DD in this browser's time zone. */
export function localDay(t: number): string {
  const d = new Date(t);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The first day a date range includes (recording dates are compared by day). */
export function dateFrom(range: DateRange, now = Date.now()): string | undefined {
  if (range === "any") return undefined;
  if (range === "today") return localDay(now);
  const days = { "7d": 7, "30d": 30, "90d": 90, "1y": 365 }[range];
  return localDay(now - days * DAY);
}

// ---------- sorting ----------

export type SortKey = "title" | "date" | "duration" | "speakers" | "status" | "importance";
export type SortDir = "asc" | "desc";

export type LibraryQuery = NonNullable<ListRecordingsData["query"]>;

/** The list endpoint's query for these filters, tab, sort and namespace. Undefined values are left out. */
export function libraryQuery(
  f: Filters,
  view: LibraryView,
  sort: { key: SortKey; dir: SortDir },
  ns: string | null,
  now = Date.now(),
): LibraryQuery {
  const q: LibraryQuery = { sort: sort.dir === "desc" ? `-${sort.key}` : sort.key };
  if (ns) q.ns = ns;
  if (f.q.trim()) q.q = f.q.trim();
  if (f.statuses.length) q.status = f.statuses;
  if (f.speaker) q.speaker = f.speaker.ids;
  const from = dateFrom(f.date, now);
  if (from) q.from = from;
  if (f.duration !== "any") {
    const [min, max] = DURATION_SECONDS[f.duration];
    if (min != null) q.min_duration = min;
    if (max != null) q.max_duration = max;
  }
  if (f.media !== "any") q.media = f.media;
  if (f.tags.length) q.tag = f.tags;
  if (f.origins.length) q.origin = f.origins;
  if (f.languages.length) q.language = f.languages;
  if (f.collection != null) q.collection = f.collection;
  if (f.field) {
    q.field = f.field.id;
    if (f.field.value.trim()) q.value = f.field.value.trim();
  }
  if (view === "attention") q.attention = true;
  if (view === "processing") q.processing = true;
  if (view === "mine") q.edited_by = "me";
  return q;
}

export type SpeakerChoice = SpeakerFilter & { recordings: number };

/** The namespaces’ speakers merged by name (each namespace has its own ids), most recordings first. */
export function speakerChoices(speakers: Pick<Speaker, "id" | "display" | "recordings">[]): SpeakerChoice[] {
  const by = new Map<string, SpeakerChoice>();
  for (const s of speakers) {
    const name = s.display.trim();
    if (!name) continue;
    const c = by.get(name) ?? { name, ids: [], recordings: 0 };
    c.ids.push(s.id);
    c.recordings += s.recordings ?? 0;
    by.set(name, c);
  }
  return [...by.values()].sort((a, b) => b.recordings - a.recordings || a.name.localeCompare(b.name));
}

/** Rows from index a to b inclusive, either direction: shift-click range selection. */
export function rangeIds(ids: number[], from: number, to: number): number[] {
  const [a, b] = from < to ? [from, to] : [to, from];
  return ids.slice(Math.max(0, a), b + 1);
}

/** Selected rows in namespaces where this person lacks a role an action needs: how many, and where. */
export function blockedBy<T extends { namespace?: string | null }>(
  rows: readonly T[],
  allowed: (row: T) => boolean,
): { count: number; namespaces: string[] } {
  const out = rows.filter((r) => !allowed(r));
  return { count: out.length, namespaces: [...new Set(out.map((r) => r.namespace ?? "?"))] };
}

/** The toast after deleting recordings: how many went, and why the first one that didn't. */
export function deletedToast(
  done: number,
  failed: readonly { title: string; message: string }[],
): { title: string; body: string; tone: "green" | "red" } {
  if (!failed.length)
    return {
      title: `Deleted ${plural(done, "recording")}`,
      body: "The media files stay where they are, and won’t be imported again.",
      tone: "green",
    };
  const total = done + failed.length;
  const why = `${failed[0].title}: ${failed[0].message}${failed.length > 1 ? ` (and ${failed.length - 1} more)` : ""}`;
  return {
    title: done
      ? `Deleted ${done} of ${plural(total, "recording")}`
      : `Couldn’t delete ${total === 1 ? "the recording" : plural(total, "recording")}`,
    body: why,
    tone: "red",
  };
}

/** Where selected recordings can move: the namespaces this person edits, other than the only one they're all in. */
export function moveTargets(editable: readonly string[], from: readonly (string | null | undefined)[]): string[] {
  const here = new Set(from);
  return editable.filter((ns) => !(here.size === 1 && here.has(ns)));
}

/** The toast after moving recordings: how many went where, and why the first one that didn't. */
export function movedToast(
  done: number,
  to: string,
  failed: readonly { title: string; message: string }[],
): { title: string; body: string; tone: "green" | "red" } {
  if (!failed.length)
    return {
      title: `Moved ${plural(done, "recording")} to ${to}`,
      body: `Analysis runs again in ${to}; their access and IIIF stay as they were.`,
      tone: "green",
    };
  const total = done + failed.length;
  return {
    title: done
      ? `Moved ${done} of ${plural(total, "recording")} to ${to}`
      : `Couldn’t move ${total === 1 ? "the recording" : plural(total, "recording")}`,
    body: `${failed[0].title}: ${failed[0].message}${failed.length > 1 ? ` (and ${failed.length - 1} more)` : ""}`,
    tone: "red",
  };
}

// ---------- tags ----------

/** One tag as the server keeps it: whitespace collapsed. */
export function cleanTag(t: string): string {
  return t.split(/\s+/).filter(Boolean).join(" ");
}

/** Tags typed into one box, separated by commas or new lines; without repeats, ignoring case. */
export function tagsFromText(text: string): string[] {
  const out = new Map<string, string>();
  for (const t of text.split(/[,\n]/).map(cleanTag)) if (t && !out.has(t.toLowerCase())) out.set(t.toLowerCase(), t);
  return [...out.values()];
}

/** The tags on selected recordings, with how many of them have each; most common first. */
export function tagsOn(rows: readonly { tags?: string[] | null }[]): { tag: string; count: number }[] {
  const by = new Map<string, { tag: string; count: number }>();
  for (const r of rows)
    for (const t of r.tags ?? []) {
      const k = t.toLowerCase();
      const cur = by.get(k);
      if (cur) cur.count++;
      else by.set(k, { tag: t, count: 1 });
    }
  return [...by.values()].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}
