/**
 * Routines as the screens show them: schedule presets and the cron they stand for, actions in plain words, and what
 * each action of a run did.
 */
import type { Tone } from "@/components/ui/badge";

export type Pick = "new" | "unprocessed" | "all";
export type SyncAction = { type: "sync"; watches?: number[] | null };
export type PipelineAction = {
  type: "pipeline";
  pipeline?: number | null;
  recordings?: Pick;
  limit?: number | null;
};
export type WorkflowAction = {
  type: "workflow";
  workflow?: number | null;
  version?: number | null;
  recordings?: Pick;
  limit?: number | null;
  propose_only?: boolean;
};
export type RoutineAction = SyncAction | PipelineAction | WorkflowAction;

// ---------- schedules ----------
export type Preset = "hourly" | "daily" | "weekdays" | "custom" | "manual";

export const PRESETS: { value: Preset; label: string; cron: string | null }[] = [
  { value: "hourly", label: "Every hour", cron: "0 * * * *" },
  { value: "daily", label: "Every day at 03:00", cron: "0 3 * * *" },
  { value: "weekdays", label: "Weekdays at 09:00", cron: "0 9 * * 1-5" },
  { value: "custom", label: "Custom (cron)", cron: null },
  { value: "manual", label: "Only by hand", cron: null },
];

const SHORTHAND: Record<string, string> = { "@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *" };

/** Which preset a saved schedule is: none is by hand, anything that isn't a preset is custom. */
export function presetOf(schedule: string | null | undefined): Preset {
  const s = (schedule ?? "").trim().replace(/\s+/g, " ");
  if (!s) return "manual";
  const cron = SHORTHAND[s.toLowerCase()] ?? s;
  return PRESETS.find((p) => p.cron === cron)?.value ?? "custom";
}

/** The schedule to save for a preset (custom: what was typed; by hand: none). */
export function scheduleFor(preset: Preset, custom: string): string | null {
  if (preset === "manual") return null;
  if (preset === "custom") return custom.trim().replace(/\s+/g, " ") || null;
  return PRESETS.find((p) => p.value === preset)?.cron ?? null;
}

// ---------- actions ----------
export const ACTION_LABEL: Record<RoutineAction["type"], string> = {
  sync: "Sync sources",
  pipeline: "Run a pipeline",
  workflow: "Run a workflow",
};

export const PICK_LABEL: Record<Pick, string> = {
  new: "new recordings (since the last run)",
  unprocessed: "recordings not processed yet",
  all: "all recordings",
};

export function newAction(type: RoutineAction["type"]): RoutineAction {
  if (type === "sync") return { type };
  if (type === "pipeline") return { type, recordings: "new" };
  return { type, workflow: null, recordings: "new" };
}

export type Names = {
  watch: (id: number) => string | undefined;
  pipeline: (id: number) => string | undefined;
  /** A workflow's name and scope, when known. */
  workflow: (id: number) => { name: string; scope?: string } | undefined;
};

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

/** One action in plain words, e.g. "Run Organise the entity graph (propose only)". */
export function actionText(a: RoutineAction, names: Names): string {
  if (a.type === "sync") {
    const w = a.watches;
    if (w == null) return "Sync every watched folder";
    if (w.length === 1) return `Sync ${names.watch(w[0]) ?? `folder #${w[0]}`}`;
    return `Sync ${plural(w.length, "watched folder")}`;
  }
  const on = PICK_LABEL[a.recordings ?? "new"];
  if (a.type === "pipeline") {
    const p =
      a.pipeline != null ? (names.pipeline(a.pipeline) ?? `pipeline #${a.pipeline}`) : "each namespace’s pipeline";
    return `Run ${p} on ${on}`;
  }
  if (a.workflow == null) return "Run a workflow (choose one)";
  const w = names.workflow(a.workflow);
  const name = w?.name ?? `workflow #${a.workflow}`;
  if (w?.scope === "graph") return `Organise the graph with ${name}${a.propose_only ? " (propose only)" : ""}`;
  return `Run ${name} on ${on}`;
}

/** What a routine does, its actions joined: "Sync every watched folder, then run …". */
export function routineText(actions: RoutineAction[], names: Names): string {
  if (!actions.length) return "Does nothing yet";
  return actions
    .map((a, i) => {
      const t = actionText(a, names);
      return i ? `then ${t[0].toLowerCase()}${t.slice(1)}` : t;
    })
    .join(", ");
}

/** Whether an action runs a graph workflow (so a run can be asked to only propose). */
export function touchesGraph(actions: RoutineAction[], names: Names): boolean {
  return actions.some(
    (a) => a.type === "workflow" && a.workflow != null && names.workflow(a.workflow)?.scope === "graph",
  );
}

/** The first thing to fix before saving, or null. */
export function actionProblem(actions: RoutineAction[]): string | null {
  if (!actions.length) return "Add at least one action.";
  const i = actions.findIndex((a) => a.type === "workflow" && a.workflow == null);
  if (i >= 0) return `Action ${i + 1}: choose a workflow.`;
  const j = actions.findIndex((a) => a.type === "sync" && a.watches != null && !a.watches.length);
  if (j >= 0) return `Action ${j + 1}: pick at least one folder, or sync them all.`;
  return null;
}

/** The actions as the API takes them: only the settings that apply. */
export function cleanActions(
  actions: RoutineAction[],
  graphWorkflow: (id: number) => boolean,
): Record<string, unknown>[] {
  return actions.map((a) => {
    if (a.type === "sync") return a.watches == null ? { type: "sync" } : { type: "sync", watches: a.watches };
    const out: Record<string, unknown> = { type: a.type };
    if (a.type === "pipeline") {
      if (a.pipeline != null) out.pipeline = a.pipeline;
      out.recordings = a.recordings ?? "new";
    } else {
      out.workflow = a.workflow;
      if (a.version != null) out.version = a.version;
      if (a.workflow != null && graphWorkflow(a.workflow)) {
        if (a.propose_only) out.propose_only = true;
        return out;
      }
      out.recordings = a.recordings ?? "new";
    }
    if (a.limit != null) out.limit = a.limit;
    return out;
  });
}

// ---------- runs ----------
export const STATUS: Record<string, { label: string; tone: Tone }> = {
  done: { label: "Done", tone: "green" },
  partly: { label: "Partly done", tone: "gate" },
  error: { label: "Failed", tone: "red" },
  running: { label: "Running", tone: "intent" },
};

export function statusOf(s: string | null | undefined) {
  return STATUS[s ?? ""] ?? { label: s ?? "—", tone: "neutral" as Tone };
}

/** What one action of a run did, e.g. "3 folders · 12 new" or "4 applied · 20 proposed". */
export function resultText(r: Record<string, unknown>): string {
  if (r.status === "error") return String(r.error ?? "failed");
  const x = (r.result ?? {}) as Record<string, unknown>;
  const n = (k: string) => (typeof x[k] === "number" ? (x[k] as number) : null);
  const parts: string[] = [];
  if (r.type === "sync") {
    parts.push(plural(n("folders") ?? 0, "folder"), `${n("new") ?? 0} new`);
    if (n("errors")) parts.push(plural(n("errors")!, "error"));
    return parts.join(" · ");
  }
  if (n("applied") != null || n("proposed") != null) {
    parts.push(`${n("applied") ?? 0} applied`, `${n("proposed") ?? 0} proposed`);
    if (n("skipped")) parts.push(`${n("skipped")} skipped`);
  } else {
    parts.push(`${n("queued") ?? 0} of ${plural(n("recordings") ?? 0, "recording")} queued`);
    if (n("errors")) parts.push(plural(n("errors")!, "error"));
  }
  const name = typeof x.workflow === "string" ? `${x.workflow}${x.version ? ` v${x.version}` : ""}: ` : "";
  return name + parts.join(" · ");
}

/** "42 s", "3 min", "1 h 5 min" between two times; "" while it runs. */
export function took(start: string | null | undefined, end: string | null | undefined): string {
  if (!start || !end) return "";
  const s = Math.max(0, Math.round((Date.parse(end) - Date.parse(start)) / 1000));
  if (Number.isNaN(s)) return "";
  if (s < 60) return `${s} s`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h${m % 60 ? ` ${m % 60} min` : ""}`;
}

/** Who asked for a run: the scheduler, or someone by hand. */
export function triggerText(trigger: string, by: string | null | undefined): string {
  if (trigger === "schedule") return "On schedule";
  return by ? `By hand · ${by}` : "By hand";
}
