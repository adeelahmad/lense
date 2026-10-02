/**
 * Workflows as the canvas shows them: what each kind of node does, its ports, what it takes in and passes on, a
 * starting graph, and the problems the backend would reject (so Publish can say what to fix first).
 */
import {
  Braces,
  CheckCheck,
  Filter,
  GitBranch,
  GitMerge,
  Gavel,
  Network,
  PlayCircle,
  Save,
  ScanSearch,
  SearchCheck,
  Sparkles,
  Tags,
  TextCursorInput,
  type LucideIcon,
} from "lucide-react";

export type WfNode = {
  id: string;
  type: string;
  config: Record<string, unknown>;
  label?: string;
  x?: number;
  y?: number;
};
export type WfEdge = { source: string; target: string; branch?: "yes" | "no" };
export type WfGraph = { nodes: WfNode[]; edges: WfEdge[] };
/** What a workflow runs on: one recording (from a pipeline), or the entity graph of namespaces (from a routine). */
export type Scope = "recording" | "graph";

type Info = {
  label: string;
  describe: string;
  icon: LucideIcon;
  tone: "blue" | "green" | "gold" | "red" | "neutral";
  inputs: number;
  outputs: string[];
  takes: string;
  gives: string;
  group: "Start" | "Entities" | "AI" | "Logic" | "Keep";
};

export const NODES: Record<string, Info> = {
  input: {
    label: "Recording",
    describe:
      "Where every workflow starts: the recording as templates see it (transcript, summary, entities, outputs…)",
    icon: PlayCircle,
    tone: "blue",
    inputs: 0,
    outputs: ["out"],
    takes: "—",
    gives: "recording",
    group: "Start",
  },
  extract_rules: {
    label: "Extract entities (rules)",
    describe: "The built-in extractor analyze uses, plus your terms and regular expressions",
    icon: ScanSearch,
    tone: "green",
    inputs: 1,
    outputs: ["out"],
    takes: "recording",
    gives: "entities[]",
    group: "Entities",
  },
  extract_llm: {
    label: "Extract entities (LLM)",
    describe: "Asks the model for entities as structured output; entities passed in are kept, corrected or added to",
    icon: Sparkles,
    tone: "gold",
    inputs: 1,
    outputs: ["out"],
    takes: "recording or entities[]",
    gives: "entities[]",
    group: "Entities",
  },
  save_entities: {
    label: "Save entities",
    describe: "Makes these the recording’s entities (people’s corrections kept), then redoes keywords and chapters",
    icon: Tags,
    tone: "green",
    inputs: 1,
    outputs: [],
    takes: "entities[]",
    gives: "entity index · graph",
    group: "Entities",
  },
  llm: {
    label: "LLM prompt",
    describe: "Renders a prompt template (what came in is {{ input }}) and passes on the model’s JSON",
    icon: Sparkles,
    tone: "gold",
    inputs: 1,
    outputs: ["out"],
    takes: "anything",
    gives: "JSON",
    group: "AI",
  },
  pick: {
    label: "Pick",
    describe: "Passes on one part of what came in, by a path like action_items.0.text",
    icon: Braces,
    tone: "neutral",
    inputs: 1,
    outputs: ["out"],
    takes: "JSON",
    gives: "a value",
    group: "Logic",
  },
  condition: {
    label: "Condition",
    describe: "Tests what came in and sends it on along yes or no",
    icon: GitBranch,
    tone: "neutral",
    inputs: 1,
    outputs: ["yes", "no"],
    takes: "anything",
    gives: "the same, on yes or no",
    group: "Logic",
  },
  merge: {
    label: "Merge",
    describe: "Joins everything that reaches it: lists into one (entities on the same line once), objects into one",
    icon: GitMerge,
    tone: "neutral",
    inputs: -1,
    outputs: ["out"],
    takes: "several",
    gives: "one",
    group: "Logic",
  },
  output: {
    label: "Save output",
    describe: "Keeps what came in as a named output of the recording (templates see it as outputs.<name>)",
    icon: Save,
    tone: "gold",
    inputs: 1,
    outputs: [],
    takes: "anything",
    gives: "outputs.<name>",
    group: "Keep",
  },
  field: {
    label: "Set field",
    describe: "Sets one of the namespace’s custom fields, checked like an edit and kept in its history",
    icon: TextCursorInput,
    tone: "gold",
    inputs: 1,
    outputs: [],
    takes: "a value",
    gives: "a custom field",
    group: "Keep",
  },
  // Graph workflows (scope graph)
  candidates: {
    label: "Look-alike entities",
    describe:
      "Pairs of entities that may be one thing, found by rules (same letters, acronym, spelling, sound, one name in the other)",
    icon: SearchCheck,
    tone: "green",
    inputs: 1,
    outputs: ["out"],
    takes: "namespaces",
    gives: "pairs[]",
    group: "Entities",
  },
  llm_judge: {
    label: "Ask the model",
    describe: "Asks the model whether each pair is the same thing, with lines where each name was said",
    icon: Gavel,
    tone: "gold",
    inputs: 1,
    outputs: ["out"],
    takes: "pairs[]",
    gives: "pairs[] with a verdict",
    group: "AI",
  },
  filter: {
    label: "Filter",
    describe: "Keeps the items of a list that pass a test, e.g. verdict.same equals true",
    icon: Filter,
    tone: "neutral",
    inputs: 1,
    outputs: ["out"],
    takes: "a list",
    gives: "the items that pass",
    group: "Logic",
  },
  apply_changes: {
    label: "Apply changes",
    describe: "Merges or links the pairs it’s sure of and proposes the rest for someone to accept; all can be undone",
    icon: CheckCheck,
    tone: "gold",
    inputs: 1,
    outputs: [],
    takes: "pairs[]",
    gives: "merges · links · proposals",
    group: "Keep",
  },
};

/** The start of a graph workflow: the namespaces a routine runs it over. */
const GRAPH_INPUT: Info = {
  ...NODES.input,
  label: "Namespaces",
  describe: "Where every graph workflow starts: the namespaces the routine runs it over",
  icon: Network,
  gives: "namespaces",
};

export function infoFor(type: string, scope: Scope = "recording"): Info | undefined {
  return type === "input" && scope === "graph" ? GRAPH_INPUT : NODES[type];
}

/** Node types per scope, for when the catalog hasn't said (it lists each type's scopes). */
const SCOPE_NODES: Record<Scope, string[]> = {
  recording: [
    "input",
    "extract_rules",
    "extract_llm",
    "save_entities",
    "llm",
    "pick",
    "condition",
    "merge",
    "output",
    "field",
  ],
  graph: ["input", "pick", "condition", "merge", "candidates", "llm_judge", "filter", "apply_changes"],
};

/** Whether a node type can be used in a workflow of this scope. */
export function inScope(type: string, scope: Scope, catalog?: { type: string; scopes?: string[] }[]): boolean {
  const t = catalog?.find((x) => x.type === type);
  return t?.scopes ? t.scopes.includes(scope) : SCOPE_NODES[scope].includes(type);
}

export const GROUPS = ["Entities", "AI", "Logic", "Keep"] as const;
export const OPS: Record<string, string> = {
  exists: "is there",
  empty: "is empty",
  equals: "equals",
  not_equals: "doesn’t equal",
  contains: "contains",
  gt: "is more than",
  lt: "is less than",
};

/** A new workflow: today’s entity extraction, ready to extend (an LLM pass, your own rules); or, for the graph,
 * look-alikes judged by the model and proposed. */
export function starter(scope: Scope = "recording"): WfGraph {
  if (scope === "graph")
    return {
      nodes: [
        { id: "in", type: "input", config: {}, x: 0, y: 120 },
        { id: "pairs", type: "candidates", config: defaultConfig("candidates"), x: 260, y: 120 },
        { id: "judge", type: "llm_judge", config: defaultConfig("llm_judge"), x: 520, y: 120 },
        { id: "same", type: "filter", config: defaultConfig("filter"), x: 780, y: 120 },
        { id: "apply", type: "apply_changes", config: {}, x: 1040, y: 120 },
      ],
      edges: [
        { source: "in", target: "pairs" },
        { source: "pairs", target: "judge" },
        { source: "judge", target: "same" },
        { source: "same", target: "apply" },
      ],
    };
  return {
    nodes: [
      { id: "in", type: "input", config: {}, x: 0, y: 120 },
      { id: "rules", type: "extract_rules", config: { builtin: true }, x: 280, y: 120 },
      { id: "keep", type: "save_entities", config: {}, x: 560, y: 120 },
    ],
    edges: [
      { source: "in", target: "rules" },
      { source: "rules", target: "keep" },
    ],
  };
}

export function defaultConfig(type: string): Record<string, unknown> {
  if (type === "extract_rules") return { builtin: true };
  if (type === "condition") return { op: "exists" };
  if (type === "output") return { key: "" };
  if (type === "candidates") return { kind: "merge", min_confidence: 0.7, limit: 100 };
  if (type === "llm_judge") return { batch: 25 };
  if (type === "filter") return { path: "verdict.same", op: "equals", value: true };
  return {};
}

export const KEY_RX = /^[a-z][a-z0-9_]{0,40}$/;
const PATH_RX = /^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$/;
const TYPE_RX = /^[A-Z][A-Z_]{0,30}$/;

/** A one-line summary of a node's settings, for the card. */
export function nodeSummary(
  n: WfNode,
  names: { template: (id: number) => string | undefined; field: (id: number) => string | undefined },
) {
  const c = n.config;
  switch (n.type) {
    case "llm":
      return c.template != null
        ? (names.template(Number(c.template)) ?? `template #${c.template}`)
        : "Choose a template";
    case "pick":
      return c.path ? `→ ${c.path}` : "Choose a path";
    case "condition":
    case "filter":
      return `${n.type === "filter" ? "keep where " : ""}${c.path ? String(c.path) : "value"} ${OPS[String(c.op)] ?? ""}${c.value != null && c.op !== "exists" && c.op !== "empty" ? ` ${JSON.stringify(c.value)}` : ""}`;
    case "extract_rules": {
      const parts = [c.builtin === false ? "your rules only" : "built-in"];
      const t = (c.terms as string[] | undefined)?.length;
      const p = (c.patterns as unknown[] | undefined)?.length;
      if (t) parts.push(`${t} terms`);
      if (p) parts.push(`${p} patterns`);
      return parts.join(" · ");
    }
    case "extract_llm":
      return (c.types as string[] | undefined)?.length ? (c.types as string[]).join(", ") : "all types";
    case "output":
      return c.key ? `outputs.${c.key}` : "Name the output";
    case "field":
      return c.field != null ? (names.field(Number(c.field)) ?? `field #${c.field}`) : "Choose a field";
    case "candidates":
      return [
        c.kind === "link" ? "across namespaces" : "in each namespace",
        `${Math.round(Number(c.min_confidence ?? 0.7) * 100)}%+`,
        `up to ${Number(c.limit ?? 100)}`,
        ...((c.types as string[] | undefined)?.length ? [(c.types as string[]).join(", ")] : []),
      ].join(" · ");
    case "llm_judge":
      return `${Number(c.batch ?? 25)} pairs a call${c.model ? ` · ${String(c.model)}` : ""}`;
    case "apply_changes":
      return c.apply_above == null
        ? "Propose every change"
        : `Make ${Math.round(Number(c.apply_above) * 100)}%+ (up to ${Number(c.max_apply ?? 50)}), propose the rest`;
    default:
      return NODES[n.type]?.describe ?? "";
  }
}

/** Problems per node id ("" for the graph as a whole), as the backend would put them. */
export function problems(
  g: WfGraph,
  templateKind: (id: number) => string | undefined,
  scope: Scope = "recording",
): Record<string, string> {
  const out: Record<string, string> = {};
  const by = new Map(g.nodes.map((n) => [n.id, n]));
  const inputs = g.nodes.filter((n) => n.type === "input");
  if (inputs.length !== 1) out[""] = `A workflow has exactly one ${infoFor("input", scope)?.label} node.`;
  for (const n of g.nodes) {
    const c = n.config;
    const into = g.edges.filter((e) => e.target === n.id);
    if (!SCOPE_NODES[scope].includes(n.type))
      out[n.id] =
        scope === "graph" ? "This node works on recordings, not the graph." : "This node is for graph workflows.";
    else if (n.type !== "input" && !into.length) out[n.id] = "Connect something into it.";
    else if (n.type !== "merge" && into.length > 1) out[n.id] = "It takes one input; join several with a Merge.";
    else if (n.type === "llm" && c.template == null) out[n.id] = "Choose a prompt template.";
    else if (n.type === "llm" && templateKind(Number(c.template)) && templateKind(Number(c.template)) !== "prompt")
      out[n.id] = "Needs a prompt template.";
    else if (n.type === "pick" && !PATH_RX.test(String(c.path ?? "")))
      out[n.id] = "Give a path like tldr or action_items.0.text.";
    else if ((n.type === "condition" || n.type === "filter") && c.path && !PATH_RX.test(String(c.path)))
      out[n.id] = "The path is letters, digits, _ and dots.";
    else if (
      (n.type === "condition" || n.type === "filter") &&
      (c.op === "gt" || c.op === "lt") &&
      typeof c.value !== "number"
    )
      out[n.id] = "Compare with a number.";
    else if (n.type === "output" && !KEY_RX.test(String(c.key ?? "")))
      out[n.id] = "Name it: lowercase letters, digits and _, e.g. meeting_notes.";
    else if (n.type === "field" && c.field == null) out[n.id] = "Choose a custom field.";
    else if (n.type === "candidates" && !between(c.min_confidence, 0, 1, false))
      out[n.id] = "How sure is a number from 0 to 1.";
    else if (n.type === "candidates" && !between(c.limit, 1, 1000, true)) out[n.id] = "At most is 1 to 1000 pairs.";
    else if (n.type === "llm_judge" && !between(c.batch, 1, 100, true)) out[n.id] = "Pairs a call is 1 to 100.";
    else if (n.type === "apply_changes" && !between(c.apply_above, 0, 1, false))
      out[n.id] = "Sure enough at is a number from 0 to 1.";
    else if (n.type === "apply_changes" && !between(c.max_apply, 0, 1000, true)) out[n.id] = "At most is 0 to 1000.";
    else if (n.type === "extract_rules") {
      for (const p of (c.patterns as { pattern?: string; type?: string }[] | undefined) ?? []) {
        if (!p.pattern) out[n.id] = "A pattern is empty.";
        else if (!TYPE_RX.test(p.type ?? "")) out[n.id] = "Each pattern needs a type in capitals, e.g. TICKET.";
        else
          try {
            new RegExp(p.pattern);
          } catch {
            out[n.id] = `Pattern ${p.pattern} doesn’t work.`;
          }
      }
    }
    for (const e of g.edges.filter((x) => x.source === n.id)) {
      if (n.type === "condition" && !e.branch) out[n.id] = "Connect its yes or its no.";
      if (!by.has(e.target)) out[n.id] = "A connection goes nowhere.";
    }
  }
  if (!out[""] && hasLoop(g)) out[""] = "The workflow has a loop.";
  if (scope === "graph") {
    if (!out[""] && !g.nodes.some((n) => n.type === "apply_changes"))
      out[""] = "Add an Apply changes node, or the workflow changes nothing.";
  } else if (!out[""] && !g.nodes.some((n) => ["output", "field", "save_entities"].includes(n.type)))
    out[""] = "Add a Save output, Set field or Save entities node, or the workflow keeps nothing.";
  return out;
}

/** Unset, or a number in [lo, hi] (a whole one when `whole`). */
function between(v: unknown, lo: number, hi: number, whole: boolean): boolean {
  if (v == null) return true;
  return typeof v === "number" && v >= lo && v <= hi && (!whole || Number.isInteger(v));
}

function hasLoop(g: WfGraph): boolean {
  const ins = new Map(g.nodes.map((n) => [n.id, 0]));
  for (const e of g.edges) ins.set(e.target, (ins.get(e.target) ?? 0) + 1);
  const ready = g.nodes.filter((n) => !ins.get(n.id)).map((n) => n.id);
  let seen = 0;
  while (ready.length) {
    const n = ready.shift()!;
    seen++;
    for (const e of g.edges)
      if (e.source === n) {
        ins.set(e.target, (ins.get(e.target) ?? 0) - 1);
        if (!ins.get(e.target)) ready.push(e.target);
      }
  }
  return seen !== g.nodes.length;
}

/** The graph as the API takes it: rounded positions, labels only when set. */
export function cleanGraph(g: WfGraph): WfGraph {
  return {
    nodes: g.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      config: n.config,
      ...(n.label?.trim() ? { label: n.label.trim() } : {}),
      x: Math.round(n.x ?? 0),
      y: Math.round(n.y ?? 0),
    })),
    edges: g.edges.map((e) => ({ source: e.source, target: e.target, ...(e.branch ? { branch: e.branch } : {}) })),
  };
}

export function sameGraph(a: WfGraph, b: WfGraph): boolean {
  return JSON.stringify(cleanGraph(a)) === JSON.stringify(cleanGraph(b));
}
