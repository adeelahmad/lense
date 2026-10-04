/**
 * Jobs as the Activity screens show them: step states, the one-line "what is happening" text, triggers, durations,
 * how each step went (from the run's step records, or parsed from the log for older runs), pipelines, time left,
 * worker health, and the list order that keeps the scroll still.
 * Pure functions only, so the page, the top-bar drawer and the tests share them.
 */
import type { Job, JobPipeline, StepOutput, WorkerInfo } from "@/app/openapi-client/types.gen";
import { approxDuration } from "@/components/batches/format";
import { STEP_LABEL } from "@/components/ui/loop";

/** A run as the API sends it. */
export type JobRecord = Job;

export const ACTIVE = ["queued", "running", "paused", "held"];
export const isActive = (status: string | null | undefined) => ACTIVE.includes(status ?? "");
export const canRetry = (status: string | null | undefined) => status === "failed" || status === "cancelled";

export type StepSpec = {
  type: string;
  name?: string;
  label: string;
  spec: Record<string, unknown>;
};

/** A step's display name: its own name, or the step type's label (Transcribe, Text on screen...). */
export function stepLabel(type: string, name?: string | null): string {
  if (name) return name;
  return STEP_LABEL[type] ?? (type ? type[0].toUpperCase() + type.slice(1) : "Step");
}

/** Steps may be plain names or {type, name, ...} specs; an unnamed LLM step goes by the output it saves ("Meeting notes"). */
export function stepSpecs(steps: unknown[] | null | undefined): StepSpec[] {
  return (steps ?? []).map((s) => {
    const spec = (typeof s === "string" ? { type: s } : ((s as Record<string, unknown>) ?? {})) as Record<
      string,
      unknown
    >;
    const type = String(spec.type ?? "step");
    const name = typeof spec.name === "string" && spec.name ? spec.name : undefined;
    const key = type === "llm" && typeof spec.key === "string" ? spec.key.replace(/[_-]+/g, " ").trim() : "";
    const label = name ?? (key ? key[0].toUpperCase() + key.slice(1) : stepLabel(type));
    return { type, name, label, spec };
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

export function elapsed(
  from: string | null | undefined,
  to: string | null | undefined,
  now = Date.now(),
): number | null {
  const a = from ? Date.parse(from) : NaN;
  if (Number.isNaN(a)) return null;
  const b = to ? Date.parse(to) : now;
  return Number.isNaN(b) ? null : Math.max(0, b - a);
}

export type Trigger = {
  kind: "watch" | "person" | "system";
  label: string;
  watch?: number;
};

/** Who or what started a job: "watch:3" is watched folder 3; an email is a person. */
export function triggerOf(
  createdBy: string | null | undefined,
  names?: Record<string, string>,
  me?: string | null,
): Trigger {
  const by = (createdBy ?? "").trim();
  const w = /^watch:(\d+)$/.exec(by);
  if (w)
    return {
      kind: "watch",
      label: `watched folder ${w[1]}`,
      watch: Number(w[1]),
    };
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
    case "held":
      return `Waiting for you: over budget · ${label} first`;
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
  /** How long it took ("<step> done in 12.3s" in older logs). */
  seconds?: number;
  outcome?: "done" | "failed" | "skipped" | "handed-off";
  /** The step's own last message ("analysed", "no LLM is configured"...), or its error. */
  note?: string;
  /** From the run's record of the step (runs since those were kept). */
  startedAt?: string;
  finishedAt?: string;
  worker?: string;
  outputs?: StepOutput[];
};

export function lineTone(text: string): LogTone {
  if (/ failed: |✕|Error\b|Traceback/.test(text)) return "fail";
  if (/ done in [\d.]+s$/.test(text)) return "ok";
  if (/skipped|^handing |^cancelled$|kept (it|them)/.test(text)) return "gate";
  return "out";
}

/**
 * New lines of a run's log arrive numbered (`start` is the number of the first one): add what's new. Null when they
 * leave a gap (lines were missed), so the caller fetches from where it got to.
 */
export function appendLog(lines: string[], start: number, more: string[]): string[] | null {
  if (start > lines.length) return null;
  if (start + more.length <= lines.length) return lines;
  return lines.slice(0, start).concat(more);
}

/**
 * Split the worker's log into one part per step. The log only has events: messages from inside a step, then
 * "<step> done in 1.2s", "<step> failed: ...", "<step> skipped: ..." or "handing <step> to a worker that can run it".
 * Retries append to the same log, so a later "done" for a step overrides an earlier failure.
 */
export function parseJobLog(
  log: string[] | null | undefined,
  steps: unknown[] | null | undefined,
): { lines: LogLine[]; byStep: StepLog[] } {
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

/**
 * How each step of a run went. Runs keep a record of each step (its times, how it ended, its last message, its lines
 * of the log and the outputs it saved); runs from before those were kept are read from the log. `log` holds the run's
 * lines from number `start` on: the whole log, or its last lines while the rest loads.
 */
export function runSteps(
  job: Pick<Job, "steps" | "step_runs">,
  log: string[] | null | undefined,
  start = 0,
): { lines: LogLine[]; byStep: StepLog[] } {
  const parsed = parseJobLog(log, job.steps);
  if (!job.step_runs) return parsed;
  const byStep = parsed.byStep.map((_, k): StepLog => {
    const r = job.step_runs?.[k];
    if (!r) return { lines: [] };
    const from = r.log_from == null ? null : Math.max(0, r.log_from - start);
    const to = r.log_to == null ? undefined : Math.max(0, r.log_to - start);
    return {
      lines: from == null ? [] : parsed.lines.slice(from, to),
      seconds: r.seconds ?? undefined,
      outcome: r.outcome === "running" ? undefined : (r.outcome ?? undefined),
      note: r.note ?? undefined,
      startedAt: r.started_at ?? undefined,
      finishedAt: r.finished_at ?? undefined,
      worker: r.worker ?? undefined,
      outputs: r.outputs ?? [],
    };
  });
  return { lines: parsed.lines, byStep };
}

/** "outputs.meeting_notes · Meeting notes v2 · model gpt-x": an output a step saved and what made it. */
export function outputText(o: StepOutput, templateName?: (id: number) => string | undefined): string {
  const made =
    o.template != null
      ? `${templateName?.(o.template) ?? `template #${o.template}`}${o.version != null ? ` v${o.version}` : ""}`
      : null;
  return [`outputs.${o.key}`, made, o.model ? `model ${o.model}` : null].filter(Boolean).join(" · ");
}

// ---------- pipelines and time left ----------

/** "Notes v3", "Standard steps", or null when the run's steps were chosen directly (a batch, Reprocess). */
export function pipelineLabel(p: JobPipeline | null | undefined): string | null {
  if (!p) return null;
  if (p.id == null) return "Standard steps";
  return p.version != null ? `${p.name} v${p.version}` : p.name;
}

/** What the Pipeline filter matches a run on: "p3:2" (pipeline 3, version 2), "standard" or "chosen". */
export function pipelineKey(p: JobPipeline | null | undefined): string {
  if (!p) return "chosen";
  return p.id == null ? "standard" : `p${p.id}:${p.version ?? ""}`;
}

/** The Pipeline filter's choices among these runs: each pipeline's versions (newest first), then the standard steps, then steps chosen directly. */
export function pipelineOptions(jobs: Pick<Job, "pipeline">[]): { value: string; label: string }[] {
  const named = new Map<string, JobPipeline>();
  let standard = false;
  let chosen = false;
  for (const j of jobs) {
    if (!j.pipeline) chosen = true;
    else if (j.pipeline.id == null) standard = true;
    else named.set(pipelineKey(j.pipeline), j.pipeline);
  }
  const out = [...named.entries()]
    .sort(([, a], [, b]) => a.name.localeCompare(b.name) || (b.version ?? 0) - (a.version ?? 0))
    .map(([value, p]) => ({ value, label: pipelineLabel(p) ?? p.name }));
  if (standard) out.push({ value: "standard", label: "Standard steps" });
  if (chosen) out.push({ value: "chosen", label: "Steps chosen directly" });
  return out;
}

/** "about 4 min left" for an active run, from how long its steps usually take; null with nothing to go by. */
export function timeLeft(job: Pick<Job, "status" | "eta_seconds">): string | null {
  if (!isActive(job.status) || job.eta_seconds == null || !Number.isFinite(job.eta_seconds)) return null;
  if (job.eta_seconds < 10) return "should finish shortly";
  return `about ${approxDuration(job.eta_seconds).replace("~", "")} left`;
}

/** "~2 min", or "<1 s" for a step that's over in a moment. */
export function approx(seconds: number): string {
  return seconds < 1 ? "<1 s" : approxDuration(seconds);
}

/** "usually ~2 min" for a step, from recent runs (none for a step with nothing to go by, or over in a moment). */
export function usually(estimate: number | null | undefined): string | null {
  return estimate == null || estimate < 1 ? null : `usually ${approxDuration(estimate)}`;
}

// ---------- workers ----------

export type WorkerState = "busy" | "idle" | "paused" | "silent";

/**
 * Workers heartbeat about every 30 s, and every 15 s while they run something; one quiet for 2 min is silent (unless
 * its job is still running: workers from before busy heartbeats only beat between jobs). A paused worker that has
 * finished its run is "paused"; while it finishes it, it's still busy.
 */
export const SILENT_AFTER_MS = 2 * 60_000;

export function workerState(
  w: Pick<WorkerInfo, "heartbeat_at" | "current" | "paused">,
  now = Date.now(),
  currentRunning = false,
): WorkerState {
  const beat = w.heartbeat_at ? Date.parse(w.heartbeat_at) : NaN;
  const busy = w.current != null && w.current !== "";
  if (busy && currentRunning) return "busy";
  if (Number.isNaN(beat) || now - beat > SILENT_AFTER_MS) return "silent";
  return busy ? "busy" : w.paused ? "paused" : "idle";
}

/** "CPU 42% · 12 steps in the last hour": a worker's machine load (from its heartbeat) and its recent work. */
export function workerLoad(w: Pick<WorkerInfo, "load" | "cpus" | "steps_last_hour">): string {
  const cpu =
    w.load == null
      ? null
      : `CPU ${Math.round(w.load * 100)}%${w.cpus ? ` of ${w.cpus} ${w.cpus === 1 ? "core" : "cores"}` : ""}`;
  const n = w.steps_last_hour ?? 0;
  return [cpu, `${n} ${n === 1 ? "step" : "steps"} in the last hour`].filter(Boolean).join(" · ");
}

/** What pausing or draining means for a worker right now; null when it's taking runs as usual. */
export function pauseNote(w: Pick<WorkerInfo, "paused" | "draining" | "current" | "paused_by">): string | null {
  if (!w.paused) return null;
  const by = w.paused_by ? ` by ${w.paused_by}` : "";
  if (w.current == null || w.current === "") return `Paused${by}: takes no new runs until resumed.`;
  return w.draining
    ? `Draining${by}: hands its run back to the queue after the step it’s on.`
    : `Paused${by}: finishes this run, then takes no new ones.`;
}

/**
 * Why a queued job is still waiting (A5): `stuck` when no live worker runs its next step, and a sentence about each
 * worker ("mac-mini is busy, gpu-box is silent, server-1 doesn’t run transcribe.").
 */
export function waitingReason(
  step: string,
  workers: WorkerInfo[],
  states: Record<string, WorkerState>,
): { stuck: boolean; text: string } {
  const label = stepLabel(step).toLowerCase();
  const live = workers.filter((w) => (w.steps ?? []).includes(step) && states[w.name] !== "silent" && !w.paused);
  const parts = workers.map((w) => {
    const st = states[w.name];
    if (!(w.steps ?? []).includes(step)) return `${w.name} doesn’t run ${label}`;
    if (st === "silent") return `${w.name} is silent`;
    if (w.paused) return `${w.name} is paused`;
    return st === "busy" ? `${w.name} is busy` : `${w.name} is free`;
  });
  return {
    stuck: !live.length,
    text: parts.length ? `${parts.join(", ")}.` : "No workers have checked in.",
  };
}

// ---------- batches ----------

export type BatchInfo = {
  id: number;
  label: string;
  status: string;
  created_by?: string | null;
  created_at?: string | null;
  progress: {
    counts: Record<string, number>;
    done: number;
    total: number;
    remaining: number;
  };
};

export type BatchAction = "pause" | "resume" | "continue" | "retry";

/** A batch as one row: its icon state, the words, and the one action that makes sense now. */
export function batchPhase(b: BatchInfo): {
  icon: string;
  text: string;
  action?: BatchAction;
  share: { done: number; failed: number; running: number };
} {
  const c = b.progress?.counts ?? {};
  const failed = c.failed ?? 0;
  const done = b.progress?.done ?? 0;
  const all = (b.progress?.total ?? 0) + (b.progress?.remaining ?? 0);
  const failedText = failed ? ` · ${failed} failed` : "";
  const share = {
    done: all ? (done - failed) / all : 0,
    failed: all ? failed / all : 0,
    running: all ? (c.running ?? 0) / all : 0,
  };
  switch (b.status) {
    case "sample":
      return {
        icon: "running",
        text: `Sample · ${done} of ${b.progress?.total ?? 0}${failedText}`,
        action: "pause",
        share,
      };
    case "sample done":
      return {
        icon: "gate",
        text: `Sample done · check it, then run the other ${b.progress?.remaining ?? 0}`,
        action: "continue",
        share,
      };
    case "running":
      return {
        icon: "running",
        text: `${done} of ${all}${failedText}`,
        action: "pause",
        share,
      };
    case "paused":
      return {
        icon: "paused",
        text: `Paused at ${done} of ${all}${failedText}`,
        action: "resume",
        share,
      };
    case "cancelled":
      return {
        icon: "cancelled",
        text: `Cancelled at ${done} of ${all}${failedText}`,
        action: failed ? "retry" : undefined,
        share,
      };
    default:
      return {
        icon: failed ? "failed" : "succeeded",
        text: `${done} of ${all} done${failedText}`,
        action: failed ? "retry" : undefined,
        share,
      };
  }
}

// ---------- list order ----------

/**
 * Rows update in place; new jobs join the top only while the list is scrolled to the top, otherwise they wait
 * behind a "2 new" pill. `order` is what is on screen; `rows` is the latest data, newest first.
 */
export function reconcileOrder<K extends string | number>(
  order: K[],
  rows: { id: K }[],
  atTop: boolean,
): { order: K[]; pending: K[] } {
  const ids = rows.map((r) => r.id);
  if (atTop || order.length === 0) return { order: ids, pending: [] };
  const present = new Set(ids);
  const known = new Set(order);
  return {
    order: order.filter((id) => present.has(id)),
    pending: ids.filter((id) => !known.has(id)),
  };
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
export function applyJobEvent<T extends { id: number; status: string }>(
  rows: T[],
  job: T,
  statuses?: string[] | null,
): T[] {
  const i = rows.findIndex((r) => r.id === job.id);
  const fits = !statuses?.length || statuses.includes(job.status);
  if (i >= 0) {
    if (!fits) return rows.filter((r) => r.id !== job.id);
    const next = rows.slice();
    next[i] = {
      ...rows[i],
      ...job,
      log: (job as { log?: unknown }).log ?? (rows[i] as { log?: unknown }).log,
    };
    return next;
  }
  return fits ? [job, ...rows] : rows;
}

/** Status counts after a live change, so the chips stay right between refetches. */
export function applyCount(
  counts: Record<string, number>,
  before: string | undefined,
  after: string,
): Record<string, number> {
  if (before === after) return counts;
  const next = { ...counts };
  if (before) next[before] = Math.max(0, (next[before] ?? 0) - 1);
  next[after] = (next[after] ?? 0) + 1;
  return next;
}
