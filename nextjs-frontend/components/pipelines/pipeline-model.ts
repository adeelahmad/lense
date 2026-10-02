/**
 * Pipelines as the editor shows them: step specs, which step needs which (so a step can't move above its input),
 * what each step makes available to later ones, and one-line summaries of settings and conditions.
 */
import { stepLabel } from "@/components/activity/job-model";

export type StepSpec = {
  type: string;
  name?: string;
  when?: {
    min_minutes?: number;
    max_minutes?: number;
    source?: string;
    languages?: string[];
  };
  template?: number;
  version?: number;
  key?: string;
  filename?: string;
  destination?: { source: number; path?: string };
  model?: string;
  force?: boolean;
  workflow?: number;
};

export function toSpec(s: unknown): StepSpec {
  if (typeof s === "string") return { type: s };
  return { ...((s as StepSpec) ?? { type: "analyze" }) };
}

/** Drop empty settings so the saved spec stays minimal (the backend rejects unknown keys, not empty ones). */
export function cleanSpec(s: StepSpec): StepSpec | string {
  const out: Record<string, unknown> = { type: s.type };
  if (s.name?.trim()) out.name = s.name.trim();
  const w: Record<string, unknown> = {};
  if (s.when?.min_minutes != null && !Number.isNaN(s.when.min_minutes)) w.min_minutes = s.when.min_minutes;
  if (s.when?.max_minutes != null && !Number.isNaN(s.when.max_minutes)) w.max_minutes = s.when.max_minutes;
  if (s.when?.source) w.source = s.when.source;
  if (s.when?.languages?.length) w.languages = s.when.languages;
  if (Object.keys(w).length) out.when = w;
  if (s.template != null) out.template = s.template;
  if (s.template != null && s.version != null) out.version = s.version;
  if (s.workflow != null) out.workflow = s.workflow;
  if (s.workflow != null && s.version != null) out.version = s.version;
  if (s.key?.trim()) out.key = s.key.trim();
  if (s.filename?.trim()) out.filename = s.filename.trim();
  if (s.destination?.source != null)
    out.destination = {
      source: s.destination.source,
      path: s.destination.path ?? "",
    };
  if (s.model?.trim()) out.model = s.model.trim();
  if (s.force) out.force = true;
  return Object.keys(out).length === 1 ? s.type : (out as StepSpec);
}

/** Which earlier steps a step reads from, when they are in the same pipeline. */
export const NEEDS: Record<string, string[]> = {
  diarize: ["transcribe"],
  ocr: ["shots"],
  faces: ["shots"],
  objects: ["shots"],
  describe: ["shots"],
  analyze: ["transcribe", "diarize"],
  summarize: ["analyze"],
  llm: ["transcribe", "diarize", "analyze"],
  report: ["analyze", "summarize", "llm"],
  export: ["analyze", "summarize", "llm"],
  workflow: ["transcribe", "diarize"],
};

/** What a step makes available to later steps and templates. */
export const PROVIDES: Record<string, string> = {
  transcribe: "transcript · segments[] · language",
  diarize: "speakers[] · turns",
  shots: "shots[] · frames",
  ocr: "text on screen",
  faces: "faces[]",
  objects: "objects[]",
  describe: "descriptions[]",
  analyze: "sections[] · entities[] · keywords[] · stats",
  summarize: "summary.*",
  llm: "outputs.<key>",
  report: "a report page",
  export: "a file",
  workflow: "outputs · fields · entities",
};

export const DESCRIBE: Record<string, string> = {
  transcribe: "Speech to text with word timings (Settings → Transcription)",
  diarize: "Who spoke when (Settings → Diarization)",
  shots: "Cuts a video into shots and keyframes",
  ocr: "Reads text on screen (Settings → Video)",
  faces: "Finds faces in keyframes (Settings → Video)",
  objects: "Finds objects (people, cars, animals …) in keyframes and pages (Settings → Video)",
  describe: "Describes each shot and page with a model that can see images (Settings → LLM provider)",
  analyze: "Chapters, entities, keywords and talk-time stats",
  summarize: "The built-in summary with the LLM in Settings",
  llm: "Your prompt template; saves a structured output",
  report: "The recording’s report page, or your report template",
  export: "Writes a file from an export template, optionally to a source",
  workflow: "Runs a workflow you drew on the canvas: outputs, custom fields and entities",
};

/**
 * Move a step, unless that would put it above a step whose output it needs (or put a step below one that needs
 * it). Returns the new order, or the reason it snapped back.
 */
export function moveStep<T extends { type: string }>(
  steps: T[],
  from: number,
  to: number,
): { steps: T[]; error?: string } {
  if (from === to || from < 0 || to < 0 || from >= steps.length || to >= steps.length) return { steps };
  const next = steps.slice();
  const [s] = next.splice(from, 1);
  next.splice(to, 0, s);
  const problem = orderProblem(next);
  if (problem) return { steps, error: problem };
  return { steps: next };
}

/** The first "X needs Y's output" violation in an order, if any. */
export function orderProblem(steps: { type: string; name?: string }[]): string | null {
  for (let i = 0; i < steps.length; i++) {
    for (const need of NEEDS[steps[i].type] ?? []) {
      const j = steps.findIndex((x) => x.type === need);
      if (j > i)
        return `${stepLabel(steps[i].type, steps[i].name)} needs ${stepLabel(need)}’s output, so it has to come after it.`;
    }
  }
  return null;
}

/** "only if ≥ 5 min · audio · en, de" */
export function whenText(when: StepSpec["when"]): string | null {
  if (!when) return null;
  const parts: string[] = [];
  const { min_minutes: a, max_minutes: b } = when;
  if (a != null && b != null) parts.push(`only if ${a}–${b} min`);
  else if (a != null) parts.push(`only if > ${a} min`);
  else if (b != null) parts.push(`only if < ${b} min`);
  if (when.source) parts.push(`only ${when.source === "audio" ? "audio" : "imported transcripts"}`);
  if (when.languages?.length) parts.push(when.languages.join(", "));
  return parts.length ? parts.join(" · ") : null;
}

/** "Meeting notes v2 · outputs.meeting_notes · only if > 5 min" */
export function stepSummary(s: StepSpec, templateName?: (id: number) => string | undefined): string {
  const parts: string[] = [];
  if (s.template != null)
    parts.push(`${templateName?.(s.template) ?? `template #${s.template}`}${s.version ? ` v${s.version}` : ""}`);
  else if (s.type === "report") parts.push("built-in report");
  if (s.type === "llm" && s.key) parts.push(`outputs.${s.key}`);
  if (s.type === "export" && s.filename) parts.push(s.filename);
  if (s.model) parts.push(s.model);
  if (s.force) parts.push("forced");
  const w = whenText(s.when);
  if (w) parts.push(w);
  return parts.join(" · ");
}

/** Problems the backend would reject, per step index (so Publish can say what to fix first). */
export function specProblems(
  steps: StepSpec[],
  templateKind: (id: number) => string | undefined,
): Record<number, string> {
  const out: Record<number, string> = {};
  steps.forEach((s, i) => {
    const need = { llm: "prompt", export: "export", report: "report" }[s.type];
    if (s.type === "workflow" && s.workflow == null) out[i] = "Choose a workflow.";
    else if (s.type === "llm" && s.template == null) out[i] = "Choose a prompt template.";
    else if (s.type === "export" && s.template == null) out[i] = "Choose an export template.";
    else if (need && s.template != null && templateKind(s.template) && templateKind(s.template) !== need)
      out[i] = `Needs a ${need} template.`;
    else if (s.type === "llm" && !/^[a-z][a-z0-9_]{0,40}$/.test(s.key ?? ""))
      out[i] = "Name the output: lowercase letters, digits and _, e.g. meeting_notes.";
    else if (s.type === "export" && !s.filename?.trim()) out[i] = "Give a file name, e.g. {{ recording.title }}.md.";
    else if (s.when?.min_minutes != null && s.when?.max_minutes != null && s.when.min_minutes > s.when.max_minutes)
      out[i] = "The minimum length is above the maximum.";
  });
  if (!steps.length) out[-1] = "A pipeline needs at least one step.";
  return out;
}

export function sameSteps(a: unknown[], b: unknown[]): boolean {
  return JSON.stringify(a.map((s) => cleanSpec(toSpec(s)))) === JSON.stringify(b.map((s) => cleanSpec(toSpec(s))));
}

export type PlNode = { id: string; step: StepSpec; x?: number; y?: number };
export type PlEdge = { source: string; target: string };
export type PlGraph = { nodes: PlNode[]; edges: PlEdge[] };

/**
 * The order a pipeline graph runs in, as the backend puts it: each step after the ones it follows, ties left to
 * right (then top to bottom). Null when the graph has a loop.
 */
export function graphOrder(g: PlGraph): PlNode[] | null {
  const pos = new Map(g.nodes.map((n, k) => [n.id, [n.x ?? 0, n.y ?? 0, k] as const]));
  const cmp = (a: string, b: string) => {
    const [ax, ay, ak] = pos.get(a)!;
    const [bx, by, bk] = pos.get(b)!;
    return ax - bx || ay - by || ak - bk;
  };
  const ins = new Map(g.nodes.map((n) => [n.id, 0]));
  for (const e of g.edges) ins.set(e.target, (ins.get(e.target) ?? 0) + 1);
  let ready = g.nodes
    .filter((n) => !ins.get(n.id))
    .map((n) => n.id)
    .sort(cmp);
  const out: string[] = [];
  while (ready.length) {
    const n = ready.shift()!;
    out.push(n);
    for (const e of g.edges)
      if (e.source === n) {
        ins.set(e.target, (ins.get(e.target) ?? 0) - 1);
        if (!ins.get(e.target)) ready = [...ready, e.target].sort(cmp);
      }
  }
  if (out.length !== g.nodes.length) return null;
  const by = new Map(g.nodes.map((n) => [n.id, n]));
  return out.map((i) => by.get(i)!);
}

export function cleanPipelineGraph(g: PlGraph) {
  return {
    nodes: g.nodes.map((n) => ({
      id: n.id,
      step: cleanSpec(n.step),
      x: Math.round(n.x ?? 0),
      y: Math.round(n.y ?? 0),
    })),
    edges: g.edges.map((e) => ({ source: e.source, target: e.target })),
  };
}
