/**
 * Jobs as the Activity screens show them: step states, the one-line "what is happening" text, triggers, durations,
 * per-step logs parsed from the worker's log, worker health, and the list order that keeps the scroll still.
 * Pure functions only, so the page, the top-bar drawer and the tests share them.
 */
import type { Job, WorkerInfo } from "@/app/openapi-client/types.gen";
import { STEP_LABEL } from "@/components/ui/loop";

/** A job with the fields the backend sends beyond the typed ones (worker, attempts, times, batch...). */
export type JobRecord = Job & {
  worker?: string | null;
  attempts?: number | null;
  batch?: number | null;
  created_by?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  cancel_requested?: boolean | null;
};

export const ACTIVE = ["queued", "running", "paused"];
export const isActive = (status: string | null | undefined) => ACTIVE.includes(status ?? "");
export const canRetry = (status: string | null | undefined) => status === "failed" || status === "cancelled";

export type StepSpec = { type: string; name?: string; label: string; spec: Record<string, unknown> };

/** A step's display name: its own name, or the step type's label (Transcribe, Text on screen...). */
export function stepLabel(type: string, name?: string | null): string {
  if (name) return name;
  return STEP_LABEL[type] ?? (type ? type[0].toUpperCase() + type.slice(1) : "Step");
}

/** Steps may be plain names or {type, name, ...} specs. */
export function stepSpecs(steps: unknown[] | null | undefined): StepSpec[] {
  return (steps ?? []).map((s) => {
    const spec = (typeof s === "string" ? { type: s } : ((s as Record<string, unknown>) ?? {})) as Record<string, unknown>;
    const type = String(spec.type ?? "step");
    const name = typeof spec.name === "string" && spec.name ? spec.name : undefined;
    return { type, name, label: stepLabel(type, name), spec };
  });
}

export type StepRunState = "done" | "running" | "failed" | "waiting" | "skipped" | "not-run";

/** One state per step, from the job's status and step index (and the log, which knows which steps skipped). */
export function stepStates(job: Pick<JobRecord, "status" | "steps" | "step_index">, logs?: StepLog[]): StepRunState[] {
  const n = (job.steps ?? []).length;
  const i = Math.min(job.step_index ?? 0, n);
  return Array.from({ length: n }, (_, k): StepRunState => {
    const skipped = logs?.[k]?.outcome === "skipped";
    if (job.status === "succeeded") return skipped ? "skipped" : "done";
    if (k < i) return skipped ? "skipped" : "done";
    if (k === i) {
      if (job.status === "running") return "running";
      if (job.status === "failed") return "failed";
      if (job.status === "cancelled") return "not-run";
    }
    return job.status === "failed" || job.status === "cancelled" ? "not-run" : "waiting";
  });
}

/** "LLMError: no model" → "no model": the exception class is noise in a one-line summary. */
export function plainError(error: string | null | undefined): string {
  return (error ?? "").replace(/^[A-Za-z_.]*(Error|Exception)\s*:\s*/, "").trim();
}

/** 108 000 ms → "1 m 48 s"; under a minute "48 s"; under ten seconds one decimal. */
export function span(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  const s = ms / 1000;
  if (s < 10) return `${s < 0.1 ? "<0.1" : s.toFixed(1).replace(/\.0$/, "")} s`;
  const whole = Math.round(s);
  if (whole < 60) return `${whole} s`;
  const m = Math.floor(whole / 60);
  if (m < 60) return `${m} m ${String(whole % 60).padStart(2, "0")} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} m`;
}

/** A duration measured from second-precision timestamps: "under a second" rather than a false "0.0 s". */
export function took(ms: number): string {
  return ms < 1000 ? "under a second" : span(ms);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "14 Sep, 16:02" (or with seconds "30 Sep, 08:30:12"), in the viewer's time zone. */
export function dayTime(iso: string | null | undefined, seconds = false): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${p(d.getHours())}:${p(d.getMinutes())}${seconds ? `:${p(d.getSeconds())}` : ""}`;
}

export function elapsed(from: string | null | undefined, to: string | null | undefined, now = Date.now()): number | null {
  const a = from ? Date.parse(from) : NaN;
  if (Number.isNaN(a)) return null;
  const b = to ? Date.parse(to) : now;
  return Number.isNaN(b) ? null : Math.max(0, b - a);
}

export type Trigger = { kind: "watch" | "person" | "system"; label: string; watch?: number };

/** Who or what started a job: "watch:3" is watched folder 3; an email is a person. */
export function triggerOf(createdBy: string | null | undefined, names?: Record<string, string>, me?: string | null): Trigger {
  const by = (createdBy ?? "").trim();
  const w = /^watch:(\d+)$/.exec(by);
  if (w) return { kind: "watch", label: `watched folder ${w[1]}`, watch: Number(w[1]) };
  if (!by) return { kind: "system", label: "System" };
  if (me && by.toLowerCase() === me.toLowerCase()) return { kind: "person", label: names?.[by] ?? "You" };
  return { kind: "person", label: names?.[by] ?? by };
}

/** The one line that always says, in words, what is happening. */
export function jobPhase(job: JobRecord, opts: { waitingFor?: string | null; now?: number } = {}): string {
  const specs = stepSpecs(job.steps);
  const n = specs.length;
  const i = Math.min(job.step_index ?? 0, Math.max(0, n - 1));
  const cur = specs[i];
  const label = cur?.label ?? stepLabel(job.next_step ?? "");
  switch (job.status) {
    case "running":
      return `${label} · step ${i + 1} of ${n}${job.cancel_requested ? " · stopping after this step" : ""}`;
    case "queued":
      if (opts.waitingFor) return `Waiting for a worker that can ${opts.waitingFor}`;
      return i > 0 ? `${label} · waiting for a worker` : `Queued · ${label} first`;
    case "paused":
      return `Paused with its batch · ${label} next`;
    case "failed": {
      const e = plainError(job.error);
      return `${label} failed${e ? `: ${e}` : ""}`;
    }
    case "succeeded": {
      const d = elapsed(job.started_at, job.finished_at, opts.now);
      return d == null ? "Succeeded" : `Succeeded in ${took(d)}`;
    }
    case "cancelled":
      return n ? `Cancelled at step ${i + 1} of ${n}` : "Cancelled";
    default:
      return job.status;
  }
}

// ---------- the worker's log ----------

export type LogTone = "out" | "ok" | "fail" | "gate" | "info";
export type LogLine = { time?: string; text: string; tone: LogTone };
export type StepLog = {
  lines: LogLine[];
  /** From "<step> done in 12.3s". */
  seconds?: number;
  outcome?: "done" | "failed" | "skipped" | "handed-off";
  /** The step's own last message ("analysed", "no LLM configured; skipped"...), or its error. */
  note?: string;
};

export function lineTone(text: string): LogTone {
  if (/ failed: |✕|Error\b|Traceback/.test(text)) return "fail";
  if (/ done in [\d.]+s$/.test(text)) return "ok";
  if (/skipped|^handing |^cancelled$|kept (it|them)/.test(text)) return "gate";
  return "out";
}

/**
 * Split the worker's log into one part per step. The log only has events: messages from inside a step, then
 * "<step> done in 1.2s", "<step> failed: ...", "<step> skipped: ..." or "handing <step> to a worker that can run it".
 * Retries append to the same log, so a later "done" for a step overrides an earlier failure.
 */
export function parseJobLog(log: string[] | null | undefined, steps: unknown[] | null | undefined): { lines: LogLine[]; byStep: StepLog[] } {
  const specs = stepSpecs(steps);
  const byStep: StepLog[] = specs.map(() => ({ lines: [] }));
  const lines: LogLine[] = [];
  const find = (name: string, from: number) => {
    const match = (s: StepSpec) => s.type === name || s.name === name;
    const k = specs.findIndex((s, idx) => idx >= from && match(s));
    return k >= 0 ? k : specs.findIndex(match);
  };
  let cur = 0;
  for (const raw of log ?? []) {
    const m = /^(\d\d:\d\d:\d\d)\s+(.*)$/.exec(raw);
    const text = (m ? m[2] : raw).trim();
    const line: LogLine = { time: m?.[1], text, tone: lineTone(text) };
    lines.push(line);
    let k = Math.min(cur, specs.length - 1);
    let done: RegExpExecArray | null;
    if ((done = /^(.+?) done in ([\d.]+)s$/.exec(text))) {
      k = find(done[1], cur);
      if (k >= 0) {
        const s = byStep[k];
        s.seconds = Number(done[2]);
        s.outcome = s.outcome === "skipped" ? "skipped" : "done";
        cur = k + 1;
      }
    } else if ((done = /^(\w+) failed: (.*)$/.exec(text))) {
      k = find(done[1], cur);
      if (k >= 0) {
        byStep[k].outcome = "failed";
        byStep[k].note = done[2];
        cur = k;
      }
    } else if ((done = /^(\w+) skipped: (.*)$/.exec(text))) {
      k = find(done[1], cur);
      if (k >= 0) {
        byStep[k].outcome = "skipped";
        byStep[k].note = done[2];
        cur = k + 1;
      }
    } else if ((done = /^handing (\w+) to a worker/.exec(text))) {
      k = find(done[1], cur);
      if (k >= 0) {
        byStep[k].outcome = "handed-off";
        cur = k;
      }
    } else if (k >= 0 && text !== "cancelled") {
      // A message from inside the running step (a new attempt starts over after a failure).
      const s = byStep[k];
      if (s.outcome === "failed" || s.outcome === "handed-off") s.outcome = undefined;
      s.note = text;
      if (/(^|[;:,]\s*)skipped\b/.test(text)) s.outcome = "skipped";
    }
    if (k >= 0 && k < byStep.length) byStep[k].lines.push(line);
  }
  return { lines, byStep };
}

// ---------- workers ----------

export type WorkerState = "busy" | "idle" | "silent";

/** Idle workers heartbeat about every 30 s; one quiet for 2 min is silent, unless it is busy with a running job. */
export const SILENT_AFTER_MS = 2 * 60_000;

export function workerState(w: Pick<WorkerInfo, "heartbeat_at" | "current">, now = Date.now(), currentRunning = false): WorkerState {
  const beat = w.heartbeat_at ? Date.parse(w.heartbeat_at) : NaN;
  const busy = w.current != null && w.current !== "";
  if (busy && currentRunning) return "busy";
  if (Number.isNaN(beat) || now - beat > SILENT_AFTER_MS) return "silent";
  return busy ? "busy" : "idle";
}

/**
 * Why a queued job is still waiting (A5): `stuck` when no live worker runs its next step, and a sentence about each
 * worker ("mac-mini is busy, gpu-box is silent, server-1 doesn’t run transcribe.").
 */
export function waitingReason(step: string, workers: WorkerInfo[], states: Record<string, WorkerState>): { stuck: boolean; text: string } {
  const label = stepLabel(step).toLowerCase();
  const live = workers.filter((w) => (w.steps ?? []).includes(step) && states[w.name] !== "silent");
  const parts = workers.map((w) => {
    const st = states[w.name];
    if (!(w.steps ?? []).includes(step)) return `${w.name} doesn’t run ${label}`;
    if (st === "silent") return `${w.name} is silent`;
    return st === "busy" ? `${w.name} is busy` : `${w.name} is free`;
  });
  return { stuck: !live.length, text: parts.length ? `${parts.join(", ")}.` : "No workers have checked in." };
}

// ---------- batches ----------

export type BatchInfo = {
  id: number;
  label: string;
  status: string;
  created_by?: string | null;
  created_at?: string | null;
  progress: { counts: Record<string, number>; done: number; total: number; remaining: number };
};

export type BatchAction = "pause" | "resume" | "continue" | "retry";

/** A batch as one row: its icon state, the words, and the one action that makes sense now. */
export function batchPhase(b: BatchInfo): { icon: string; text: string; action?: BatchAction; share: { done: number; failed: number; running: number } } {
  const c = b.progress?.counts ?? {};
  const failed = c.failed ?? 0;
  const done = b.progress?.done ?? 0;
  const all = (b.progress?.total ?? 0) + (b.progress?.remaining ?? 0);
  const failedText = failed ? ` · ${failed} failed` : "";
  const share = { done: all ? (done - failed) / all : 0, failed: all ? failed / all : 0, running: all ? (c.running ?? 0) / all : 0 };
  switch (b.status) {
    case "sample":
      return { icon: "running", text: `Sample · ${done} of ${b.progress?.total ?? 0}${failedText}`, action: "pause", share };
    case "sample done":
      return { icon: "gate", text: `Sample done · check it, then run the other ${b.progress?.remaining ?? 0}`, action: "continue", share };
    case "running":
      return { icon: "running", text: `${done} of ${all}${failedText}`, action: "pause", share };
    case "paused":
      return { icon: "paused", text: `Paused at ${done} of ${all}${failedText}`, action: "resume", share };
    case "cancelled":
      return { icon: "cancelled", text: `Cancelled at ${done} of ${all}${failedText}`, action: failed ? "retry" : undefined, share };
    default:
      return { icon: failed ? "failed" : "succeeded", text: `${done} of ${all} done${failedText}`, action: failed ? "retry" : undefined, share };
  }
}

// ---------- list order ----------

/**
 * Rows update in place; new jobs join the top only while the list is scrolled to the top, otherwise they wait
 * behind a "2 new" pill. `order` is what is on screen; `rows` is the latest data, newest first.
 */
export function reconcileOrder<K extends string | number>(order: K[], rows: { id: K }[], atTop: boolean): { order: K[]; pending: K[] } {
  const ids = rows.map((r) => r.id);
  if (atTop || order.length === 0) return { order: ids, pending: [] };
  const present = new Set(ids);
  const known = new Set(order);
  return { order: order.filter((id) => present.has(id)), pending: ids.filter((id) => !known.has(id)) };
}

/** "3 min ago" within a day, else "14 Sep, 16:02". */
export function shortWhen(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  if (now - t < 86_400_000 && now - t > -60_000) {
    const s = Math.round((now - t) / 1000);
    if (s < 45) return "just now";
    const m = Math.round(s / 60);
    return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
  }
  return dayTime(iso);
}

/** Apply one live change to a list (newest first): replace in place, or add at the top when it matches the filter. */
export function applyJobEvent<T extends { id: number; status: string }>(rows: T[], job: T, statuses?: string[] | null): T[] {
  const i = rows.findIndex((r) => r.id === job.id);
  const fits = !statuses?.length || statuses.includes(job.status);
  if (i >= 0) {
    if (!fits) return rows.filter((r) => r.id !== job.id);
    const next = rows.slice();
    next[i] = { ...rows[i], ...job, log: (job as { log?: unknown }).log ?? (rows[i] as { log?: unknown }).log };
    return next;
  }
  return fits ? [job, ...rows] : rows;
}

/** Status counts after a live change, so the chips stay right between refetches. */
export function applyCount(counts: Record<string, number>, before: string | undefined, after: string): Record<string, number> {
  if (before === after) return counts;
  const next = { ...counts };
  if (before) next[before] = Math.max(0, (next[before] ?? 0) - 1);
  next[after] = (next[after] ?? 0) + 1;
  return next;
}
