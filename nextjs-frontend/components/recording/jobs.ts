/**
 * What the recording's jobs mean for the page: which state it is in (processing, analysing, failed, ready), the step
 * loop to draw, per-step notes and times from a job's log, and the Reprocess picker's step rules. Pure functions.
 */
import type { Job, JobPipeline, StepRun } from "@/app/openapi-client/types.gen";
import type { LoopStep } from "@/components/ui/loop";
import { STEP_LABEL, STEP_TONE } from "@/components/ui/loop";

export type StepSpec = {
  type: string;
  name?: string;
  key?: string;
  template?: number;
  version?: number;
  model?: string;
};

export type JobInfo = {
  id: number;
  status: string;
  steps: StepSpec[];
  stepIndex: number;
  nextStep: string | null;
  error: string | null;
  worker: string | null;
  attempts: number;
  createdBy: string | null;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  progress: number;
  log: string[];
  /** How each step went, from the run's own records (null for runs from before those were kept). */
  stepRuns: (StepRun | null)[] | null;
  /** The pipeline (and version) it runs; null when its steps were chosen directly. */
  pipeline: JobPipeline | null;
};

const ACTIVE = new Set(["queued", "running"]);

export function normalizeJob(j: Job): JobInfo {
  const o = j as Record<string, unknown>;
  const steps = (Array.isArray(j.steps) ? j.steps : []).map((s): StepSpec => {
    if (typeof s === "string") return { type: s };
    const x = (s && typeof s === "object" ? s : {}) as Record<string, unknown>;
    return {
      type: String(x.type ?? "step"),
      name: typeof x.name === "string" ? x.name : undefined,
      key: typeof x.key === "string" ? x.key : undefined,
      template: typeof x.template === "number" ? x.template : undefined,
      version: typeof x.version === "number" ? x.version : undefined,
      model: typeof x.model === "string" ? x.model : undefined,
    };
  });
  const s = (k: string) => (typeof o[k] === "string" ? (o[k] as string) : null);
  return {
    id: j.id,
    status: j.status,
    steps,
    stepIndex: Math.max(0, Math.min(j.step_index ?? 0, steps.length)),
    nextStep: j.next_step ?? null,
    error: j.error ?? null,
    worker: s("worker"),
    attempts: typeof o.attempts === "number" ? o.attempts : 0,
    createdBy: s("created_by"),
    createdAt: s("created_at"),
    startedAt: s("started_at"),
    finishedAt: s("finished_at"),
    progress: typeof j.progress === "number" ? j.progress : 0,
    log: Array.isArray(j.log) ? j.log : [],
    stepRuns: Array.isArray(j.step_runs) ? j.step_runs : null,
    pipeline: j.pipeline ?? null,
  };
}

export const isActive = (j: Pick<JobInfo, "status"> | null | undefined) => Boolean(j && ACTIVE.has(j.status));

export function stepLabel(s: StepSpec | string): string {
  const spec = typeof s === "string" ? { type: s } : s;
  if (spec.name) return spec.name;
  if (spec.type === "llm" && spec.key) return humanize(spec.key);
  return STEP_LABEL[spec.type] ?? humanize(spec.type);
}

export function humanize(key: string): string {
  const t = key.replace(/[_-]+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : key;
}

/** The step a job is on (or failed at), by type. */
export function currentStep(j: JobInfo): string | null {
  return j.nextStep ?? j.steps[j.stepIndex]?.type ?? null;
}

/**
 * The four-colour step loop for a job: done ✓ before the current step, the current one ringed while it runs,
 * ✕ where it failed (later steps skipped), everything done when it succeeded.
 */
export function loopSteps(j: JobInfo, notes?: StepNote[]): LoopStep[] {
  return j.steps.map((spec, i) => {
    let state: LoopStep["state"] = "todo";
    if (j.status === "succeeded" || i < j.stepIndex) state = "done";
    else if (i === j.stepIndex)
      state = j.status === "failed" ? "failed" : j.status === "cancelled" ? "skipped" : "current";
    else if (j.status === "failed" || j.status === "cancelled") state = "skipped";
    const note = notes?.[i];
    const skippedItself = state === "done" && Boolean(note?.skipped); // it had nothing to do (no LLM, not a video...)
    if (skippedItself) state = "skipped";
    let sub: string | undefined;
    if (state === "current")
      sub = j.status === "queued" ? (i === 0 && !j.startedAt ? "queued" : "waiting for a worker") : "running";
    else if (state === "failed") sub = shortError(j.error) ?? "failed";
    else if (skippedItself) sub = "skipped";
    else if (state === "skipped") sub = j.status === "cancelled" ? "cancelled" : "skipped";
    else if (state === "done") sub = note?.seconds != null ? duration(note.seconds) : undefined;
    else if (i === j.stepIndex + 1 && isActive(j)) sub = "waiting";
    return {
      key: spec.type,
      label: stepLabel(spec),
      tone: STEP_TONE[spec.type] ?? "neutral",
      state,
      sub,
    };
  });
}

/** "Provider timed out after 120 s" from "TimeoutError: provider timed out after 120 s". */
export function shortError(e: string | null | undefined): string | null {
  if (!e) return null;
  const m = e.replace(/^[A-Za-z_.]*(Error|Exception|Exit)\b:?\s*/, "").trim() || e;
  return m.length > 60 ? `${m.slice(0, 57)}…` : m;
}

export function duration(seconds: number): string {
  if (seconds < 1) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds * 10) / 10} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} m` : `${m} m ${s} s`;
}

export type StepNote = {
  seconds: number | null;
  notes: string[];
  skipped: boolean;
};

const DONE_RX = /^(?:\d\d:\d\d:\d\d\s+)?(.+?) done in ([\d.]+)s$/;
const SKIP_RX = /skipped|nothing to transcribe|kept them|kept it/i;

/**
 * Per-step notes and times: from the run's records of its steps, or, for runs from before those were kept, from its
 * log. There each step logs what it did, then "<step> done in 1.2s" (or "<step> skipped: why"); lines before that
 * marker belong to the step. Returns one entry per job step (null times for steps that haven't run).
 */
/** Why a step was skipped, from its note ("objects skipped: no detector", or just "no detector"); null without one. */
export function skipReason(note: string): string | null {
  const t = note.replace(/^[\w-]+ skipped:\s*/i, "").trim();
  return t || null;
}

/** What a run's visual steps said: why text on screen or objects weren't read (their step's skip note), whether faces
 * found none. */
export function visualNotes(j: JobInfo | null | undefined): {
  ocrWhy: string | null;
  noFaces: boolean;
  objectsWhy: string | null;
} {
  if (!j) return { ocrWhy: null, noFaces: false, objectsWhy: null };
  const notes = stepNotes(j);
  const at = (type: string) => notes[j.steps.findIndex((s) => s.type === type)];
  const note = (type: string) => at(type)?.notes.join(" ") ?? "";
  return {
    ocrWhy: at("ocr")?.skipped ? skipReason(note("ocr")) : null,
    noFaces: /no faces found/i.test(note("faces")),
    objectsWhy: at("objects")?.skipped ? skipReason(note("objects")) : null,
  };
}

export function stepNotes(j: JobInfo): StepNote[] {
  if (j.stepRuns)
    return j.steps.map((_, k) => {
      const r = j.stepRuns?.[k];
      if (!r || r.outcome === "running") return { seconds: null, notes: [], skipped: false };
      return { seconds: r.seconds ?? null, notes: r.note ? [r.note] : [], skipped: r.outcome === "skipped" };
    });
  const out: StepNote[] = j.steps.map(() => ({
    seconds: null,
    notes: [],
    skipped: false,
  }));
  let i = 0;
  let pending: string[] = [];
  for (const raw of j.log) {
    const line = raw.replace(/^\d\d:\d\d:\d\d\s+/, "").trim();
    if (!line) continue;
    const m = DONE_RX.exec(line);
    if (m && i < out.length) {
      out[i] = {
        seconds: Number(m[2]),
        notes: pending,
        skipped: pending.some((p) => SKIP_RX.test(p)),
      };
      pending = [];
      i++;
    } else if (/^handing .* to a worker|^cancelled$/.test(line)) continue;
    else if (/^\w+ skipped: /.test(line) && i < out.length) {
      out[i] = { seconds: null, notes: [line], skipped: true };
      i++;
    } else pending.push(line);
  }
  if (pending.length && i < out.length) out[i] = { ...out[i], notes: pending };
  return out;
}

// ---------- the page's state ----------

export type Phase = "ready" | "processing" | "analyzing" | "failed";

export type PageState = {
  phase: Phase;
  /** The job the banner or step loop is about. */
  job: JobInfo | null;
  /** For failures: the step that failed (type) and the message. */
  failedStep: string | null;
  error: string | null;
  /** Imported and never analysed: the "just imported" banner. */
  justImported: boolean;
};

const TRANSCRIBING = new Set(["transcribe", "diarize"]);

/**
 * Newest job first. An active job decides: transcribing/diarizing is "processing" (R5), anything later is "analyzing"
 * (R8). Otherwise a failed newest job (or a recording in error) is "failed" (R6).
 */
export function pageState(
  recording: {
    status?: string | null;
    error?: string | null;
    source?: string | null;
  },
  jobs: JobInfo[],
): PageState {
  const sorted = [...jobs].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? "") || b.id - a.id);
  const active = sorted.find(isActive) ?? null;
  const status = (recording.status ?? "").toLowerCase();
  // a file of its own to work through: audio or video, a document or an image
  const filed = ["audio", "document", "image"].includes(recording.source ?? "");
  const justImported = !filed && (status === "transcribed" || status === "diarized" || status === "new");
  if (active) {
    const step = currentStep(active);
    const phase: Phase = step && TRANSCRIBING.has(step) && filed ? "processing" : "analyzing";
    return { phase, job: active, failedStep: null, error: null, justImported };
  }
  const latest = sorted[0] ?? null;
  if (latest?.status === "failed")
    return {
      phase: "failed",
      job: latest,
      failedStep: currentStep(latest),
      error: latest.error,
      justImported: false,
    };
  if (status === "error")
    return {
      phase: "failed",
      job: null,
      failedStep: "transcribe",
      error: recording.error ?? null,
      justImported: false,
    };
  return {
    phase: "ready",
    job: latest,
    failedStep: null,
    error: null,
    justImported: false,
  };
}

/** What still works after a failure: the labels of the steps that finished before it. */
export function readyBefore(j: JobInfo): string[] {
  return j.steps.slice(0, j.stepIndex).map((s) => stepLabel(s));
}

// ---------- Reprocess: choose steps ----------

export const STEP_ORDER = [
  "transcribe",
  "diarize",
  "shots",
  "ocr",
  "faces",
  "objects",
  "analyze",
  "summarize",
  "report",
] as const;
export type StepKey = (typeof STEP_ORDER)[number];

export const STEP_HELP: Record<StepKey, string> = {
  transcribe: "Audio → text with timings",
  diarize: "Split speakers and match voice IDs",
  shots: "Scene cuts, keyframes and sampled frames",
  ocr: "Read text on screen from the sampled frames",
  faces: "Detect people on screen (where the namespace allows it)",
  objects: "Find objects (people, cars, animals …) on the sampled frames or pages",
  analyze: "Entities, chapters, keywords, talk-time stats",
  summarize: "Summary, topics and action items (needs an LLM)",
  report: "Recording report",
};

/** Ticking a step also ticks the later steps that use its output. */
export const DEPENDENTS: Record<StepKey, StepKey[]> = {
  transcribe: ["diarize", "analyze", "summarize", "report"],
  diarize: ["analyze", "summarize", "report"],
  shots: ["ocr", "faces", "objects"],
  ocr: [],
  faces: [],
  objects: [],
  analyze: ["report"],
  summarize: ["report"],
  report: [],
};

export type StepOption = { key: StepKey; disabled: string | null };

/**
 * The steps offered for a recording: video steps only for videos (faces and objects also for a document's or an
 * image's pages); transcribe/diarize need audio.
 */
export function reprocessOptions(opts: { video: boolean; hasAudio: boolean; paged?: boolean }): StepOption[] {
  const left = opts.video ? [] : opts.paged ? ["shots", "ocr"] : ["shots", "ocr", "faces", "objects"];
  return STEP_ORDER.filter((k) => !left.includes(k)).map((key) => {
    let disabled: string | null = null;
    if (!opts.hasAudio && key === "transcribe")
      disabled = "This recording has no audio: its transcript was imported, so there's nothing to transcribe.";
    else if (!opts.hasAudio && key === "diarize")
      disabled = "Speakers come from the imported transcript; diarizing needs the audio.";
    return { key, disabled };
  });
}

/** Toggle a step: ticking adds its (enabled) dependents, unticking removes only that step. */
export function toggleStep(
  selected: ReadonlySet<StepKey>,
  key: StepKey,
  on: boolean,
  options: StepOption[],
): Set<StepKey> {
  const enabled = new Set(options.filter((o) => !o.disabled).map((o) => o.key));
  const next = new Set(selected);
  if (!on) {
    next.delete(key);
    return next;
  }
  if (!enabled.has(key)) return next;
  next.add(key);
  for (const d of DEPENDENTS[key]) if (enabled.has(d)) next.add(d);
  return next;
}

/** Selected steps in pipeline order, ready for POST /recordings/{id}/reprocess. */
export function orderedSteps(selected: ReadonlySet<StepKey>): StepKey[] {
  return STEP_ORDER.filter((k) => selected.has(k));
}
