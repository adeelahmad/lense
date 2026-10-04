/**
 * Workflows as the canvas shows them: what each kind of node does, its ports, what it takes in and passes on, a
 * starting graph, and the problems the backend would reject (so Publish can say what to fix first).
 */
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Braces,
  CheckCheck,
  FileText,
  Group,
  ListTree,
  Puzzle,
  Repeat,
  Split,
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
  Wrench,
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
/** An edge from an output port of one node (`port`, default out) to an input port of another (`input`, default in).
 * Old graphs said a condition's yes or no as `branch`; normalize() reads it as the port. */
export type WfEdge = { source: string; target: string; port?: string; input?: string; branch?: string };
export type WfGraph = { nodes: WfNode[]; edges: WfEdge[] };
/** What a workflow runs on: one recording (from a pipeline), or the entity graph of namespaces (from a routine); or
 * an assistant tool drawn on the canvas, from its parameters to what it gives back. */
export type Scope = "recording" | "graph" | "tool";

type Info = {
  label: string;
  describe: string;
  icon: LucideIcon;
  tone: "blue" | "green" | "gold" | "red" | "neutral";
  inputs: number;
  outputs: string[];
  takes: string;
  gives: string;
  group: "Start" | "Entities" | "AI" | "Logic" | "Data" | "Loops" | "Body" | "Keep";
};

export const NODES: Record<string, Info> = {
  ask_model: {
    label: "Ask the model",
    describe: "Writes a prompt (what came in is {{ input }}) and passes on the model’s reply, as text or JSON",
    icon: Sparkles,
    tone: "gold",
    inputs: 1,
    outputs: ["out"],
    takes: "anything",
    gives: "text or JSON",
    group: "AI",
  },
  call_tool: {
    label: "Call a tool",
    describe:
      "Calls one of the assistant’s tools, with what came in (an object) over the arguments set here; tools that change something ask first",
    icon: Wrench,
    tone: "blue",
    inputs: 1,
    outputs: ["out"],
    takes: "arguments",
    gives: "the tool’s result",
    group: "AI",
  },
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
  switch: {
    label: "Switch",
    describe: "Tests cases in turn and sends what came in along the first that matches, or along default",
    icon: Split,
    tone: "neutral",
    inputs: 1,
    outputs: ["default"],
    takes: "anything",
    gives: "the same, on one case",
    group: "Logic",
  },
  set: {
    label: "Set",
    describe:
      "Builds an object from fields: paths into what came in, fixed values or templates; can take several inputs",
    icon: ListTree,
    tone: "neutral",
    inputs: 1,
    outputs: ["out"],
    takes: "one or more inputs",
    gives: "an object",
    group: "Data",
  },
  template: {
    label: "Template",
    describe: "Renders text with what came in as {{ input }} (and the recording); can read the result as JSON",
    icon: FileText,
    tone: "neutral",
    inputs: 1,
    outputs: ["out"],
    takes: "anything",
    gives: "text or JSON",
    group: "Data",
  },
  for_each: {
    label: "For each",
    describe:
      "Runs its body once per item of a list and passes on what the body returned for each (double-click to open)",
    icon: Repeat,
    tone: "blue",
    inputs: 1,
    outputs: ["out"],
    takes: "a list",
    gives: "a list",
    group: "Loops",
  },
  repeat: {
    label: "Repeat",
    describe:
      "Runs its body again on its own result until a test passes or it has run enough rounds (double-click to open)",
    icon: Repeat,
    tone: "blue",
    inputs: 1,
    outputs: ["out"],
    takes: "a starting value",
    gives: "the last result",
    group: "Loops",
  },
  group: {
    label: "Group",
    describe:
      "Nodes folded into one; its inputs and outputs are the body’s Input and Return nodes (double-click to open)",
    icon: Group,
    tone: "neutral",
    inputs: 1,
    outputs: [],
    takes: "its inputs",
    gives: "its outputs",
    group: "Loops",
  },
  custom: {
    label: "Custom node",
    describe: "A saved body of nodes, used like any other node",
    icon: Puzzle,
    tone: "neutral",
    inputs: 1,
    outputs: [],
    takes: "its inputs",
    gives: "its outputs",
    group: "Loops",
  },
  arg: {
    label: "Input",
    describe: "Where a body starts: one of its inputs (in a loop: item, index or input; in a custom node, any name)",
    icon: ArrowDownToLine,
    tone: "blue",
    inputs: 0,
    outputs: ["out"],
    takes: "—",
    gives: "an input",
    group: "Body",
  },
  return: {
    label: "Return",
    describe: "What a body gives back, on the output named here (a loop’s body returns out)",
    icon: ArrowUpFromLine,
    tone: "blue",
    inputs: 1,
    outputs: [],
    takes: "anything",
    gives: "an output",
    group: "Body",
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
    group: "Data",
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

/** The building blocks every workflow has. */
export const PRIMITIVES = [
  "input",
  "pick",
  "condition",
  "switch",
  "merge",
  "set",
  "template",
  "filter",
  "for_each",
  "repeat",
  "group",
  "custom",
  "arg",
  "return",
];

/** Node types per scope, for when the catalog hasn't said (it lists each type's scopes). */
const SCOPE_NODES: Record<Scope, string[]> = {
  recording: [...PRIMITIVES, "extract_rules", "extract_llm", "save_entities", "llm", "output", "field"],
  graph: [...PRIMITIVES, "candidates", "llm_judge", "apply_changes"],
  tool: [...PRIMITIVES, "ask_model", "call_tool"],
};

/** Whether a node type can be used in a workflow of this scope. */
export function inScope(type: string, scope: Scope, catalog?: { type: string; scopes?: string[] }[]): boolean {
  const t = catalog?.find((x) => x.type === type);
  return t?.scopes ? t.scopes.includes(scope) : SCOPE_NODES[scope].includes(type);
}

export const GROUPS = ["Entities", "AI", "Logic", "Data", "Loops", "Keep"] as const;
/** Nodes that keep something: a workflow needs one. */
export const KEEPS = ["output", "field", "save_entities", "apply_changes"];
/** Nodes with a body of nodes inside, opened by double-clicking. */
export const BODIES = ["for_each", "repeat", "group"];
/** What a loop's body starts from. */
export const LOOP_ARGS: Record<string, string[]> = {
  for_each: ["item", "index", "input"],
  repeat: ["state", "round", "input"],
};
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

/** A new tool's graph: its text goes to the model, and the reply is what it gives back. */
export function starterTool(): WfGraph {
  return {
    nodes: [
      { id: "text", type: "arg", config: { name: "text" }, x: 0, y: 120 },
      { id: "ask", type: "ask_model", config: { prompt: "Summarise in one line: {{ input }}" }, x: 300, y: 120 },
      { id: "out", type: "return", config: { name: "out" }, x: 600, y: 120 },
    ],
    edges: [
      { source: "text", target: "ask" },
      { source: "ask", target: "out" },
    ],
  };
}

/** A starting body for a loop or group: its inputs on the left, a Return on the right. */
export function starterBody(type: string): WfGraph {
  const args = LOOP_ARGS[type]?.slice(0, 1) ?? ["in"];
  return {
    nodes: [
      ...args.map((name, k) => ({ id: name, type: "arg", config: { name }, x: 0, y: 80 + 140 * k })),
      { id: "ret", type: "return", config: { name: "out" }, x: 320, y: 80 },
    ],
    edges: [{ source: args[0], target: "ret" }],
  };
}

export function defaultConfig(type: string): Record<string, unknown> {
  if (type === "extract_rules") return { builtin: true };
  if (type === "condition") return { op: "exists" };
  if (type === "output") return { key: "" };
  if (type === "candidates") return { kind: "merge", min_confidence: 0.7, limit: 100 };
  if (type === "llm_judge") return { batch: 25 };
  if (type === "filter") return { path: "verdict.same", op: "equals", value: true };
  if (type === "switch") return { cases: [{ port: "first", op: "exists" }] };
  if (type === "set") return { fields: [{ key: "value", path: "" }] };
  if (type === "template") return { template: "{{ input }}" };
  if (type === "ask_model") return { prompt: "{{ input }}" };
  if (type === "call_tool") return { tool: "search_transcripts" };
  if (type === "for_each") return { body: starterBody("for_each") };
  if (type === "repeat") return { body: starterBody("repeat"), max_rounds: 5 };
  if (type === "group") return { body: starterBody("group") };
  if (type === "arg") return { name: "in" };
  if (type === "return") return { name: "out" };
  return {};
}

export const KEY_RX = /^[a-z][a-z0-9_]{0,40}$/;
export const NAME_RX = /^[a-z][a-z0-9_]{0,30}$/;
const PATH_RX = /^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$/;
const TYPE_RX = /^[A-Z][A-Z_]{0,30}$/;

/** A custom node as the catalog lists it (enough to draw and run it). */
export type CustomDef = {
  id: number;
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  version: number;
  current: number;
  inputs: string[];
  outputs: string[];
  scopes: string[];
  keeps?: boolean;
  params?: {
    name: string;
    label?: string | null;
    kind?: string;
    default?: unknown;
    options?: unknown[] | null;
    help?: string | null;
  }[];
  visibility?: string;
  namespaces?: string[];
  owner_email?: string | null;
  editable?: boolean;
};

/** The names of a body's Input and Return nodes, top to bottom: its input and output ports. */
export function bodyPorts(body: WfGraph | undefined): { inputs: string[]; outputs: string[] } {
  const names = (t: string) => [
    ...new Set(
      (body?.nodes ?? [])
        .filter((n) => n.type === t)
        .sort((a, b) => (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0))
        .map((n) => String(n.config.name ?? "")),
    ),
  ];
  return { inputs: names("arg"), outputs: names("return") };
}

/** A node's ports: its inputs (and whether its one input takes several connections) and its outputs. */
export function portsOf(
  n: WfNode,
  custom?: (id: number) => CustomDef | undefined,
): { inputs: string[]; outputs: string[]; many: boolean } {
  const c = n.config;
  switch (n.type) {
    case "input":
    case "arg":
      return { inputs: [], outputs: ["out"], many: false };
    case "return":
      return { inputs: ["in"], outputs: [], many: false };
    case "condition":
      return { inputs: ["in"], outputs: ["yes", "no"], many: false };
    case "switch":
      return {
        inputs: ["in"],
        outputs: [...((c.cases as { port: string }[] | undefined) ?? []).map((k) => k.port), "default"],
        many: false,
      };
    case "merge":
      return { inputs: ["in"], outputs: ["out"], many: true };
    case "set":
      return {
        inputs: (c.inputs as string[] | undefined)?.length ? (c.inputs as string[]) : ["in"],
        outputs: ["out"],
        many: false,
      };
    case "group": {
      const p = bodyPorts(c.body as WfGraph | undefined);
      return { ...p, many: false };
    }
    case "custom": {
      const d = custom?.(Number(c.node));
      return { inputs: d?.inputs ?? ["in"], outputs: d?.outputs ?? [], many: false };
    }
  }
  if (KEEPS.includes(n.type)) return { inputs: ["in"], outputs: [], many: false };
  return { inputs: ["in"], outputs: ["out"], many: false };
}

/** An edge's ports, with an old condition edge's branch read as its port. */
export function edgePorts(e: WfEdge): { port: string; input: string } {
  return { port: e.port ?? e.branch ?? "out", input: e.input ?? "in" };
}

/** A graph as the canvas edits it: edges with ports, bodies too. */
export function normalize(g: WfGraph): WfGraph {
  return {
    nodes: g.nodes.map((n) =>
      BODIES.includes(n.type) && n.config.body
        ? { ...n, config: { ...n.config, body: normalize(n.config.body as WfGraph) } }
        : n,
    ),
    edges: g.edges.map((e) => {
      const { port, input } = edgePorts(e);
      return {
        source: e.source,
        target: e.target,
        ...(port !== "out" ? { port } : {}),
        ...(input !== "in" ? { input } : {}),
      };
    }),
  };
}

/** The graph at a path of body node ids ([] is the workflow itself). */
export function bodyAt(g: WfGraph, path: string[]): WfGraph | undefined {
  let cur: WfGraph | undefined = g;
  for (const id of path) cur = cur?.nodes.find((n) => n.id === id)?.config.body as WfGraph | undefined;
  return cur;
}

/** The graph with the body at `path` replaced. */
export function withBody(g: WfGraph, path: string[], body: WfGraph): WfGraph {
  if (!path.length) return body;
  const [head, ...rest] = path;
  return {
    ...g,
    nodes: g.nodes.map((n) =>
      n.id === head ? { ...n, config: { ...n.config, body: withBody(n.config.body as WfGraph, rest, body) } } : n,
    ),
  };
}

const scalar = (v: unknown) => ["string", "number", "boolean"].includes(typeof v);
const paramRef = (v: unknown): string | undefined =>
  v &&
  typeof v === "object" &&
  !Array.isArray(v) &&
  Object.keys(v).length === 1 &&
  typeof (v as { $param?: unknown }).$param === "string"
    ? (v as { $param: string }).$param
    : undefined;
export { paramRef };

/**
 * Selected nodes folded into a body: what came in from outside becomes Input nodes (one per outside port), what went
 * out becomes Return nodes. Returns the body, and how to wire a node standing in for the selection.
 */
export function foldSelection(
  g: WfGraph,
  ids: string[],
):
  | {
      body: WfGraph;
      ins: { name: string; source: string; port: string }[];
      outs: { name: string; target: string; input: string; from: string }[];
    }
  | string {
  const inside = new Set(ids);
  if (g.nodes.some((n) => inside.has(n.id) && (n.type === "input" || n.type === "arg" || n.type === "return")))
    return "Leave the workflow’s start, Input and Return nodes out of the selection.";
  const nodes = g.nodes.filter((n) => inside.has(n.id));
  if (!nodes.length) return "Select the nodes to fold first (Shift-drag, or Ctrl-click).";
  const minX = Math.min(...nodes.map((n) => n.x ?? 0));
  const minY = Math.min(...nodes.map((n) => n.y ?? 0));
  const body: WfGraph = {
    nodes: nodes.map((n) => ({ ...n, x: (n.x ?? 0) - minX + 300, y: (n.y ?? 0) - minY + 40 })),
    edges: g.edges.filter((e) => inside.has(e.source) && inside.has(e.target)),
  };
  const ins: { name: string; source: string; port: string }[] = [];
  const outs: { name: string; target: string; input: string; from: string }[] = [];
  const argFor = new Map<string, string>();
  for (const e of g.edges.filter((x) => !inside.has(x.source) && inside.has(x.target))) {
    const { port, input } = edgePorts(e);
    const key = `${e.source}:${port}`;
    if (!argFor.has(key)) {
      const name = ins.length ? `in${ins.length + 1}` : "in";
      argFor.set(key, name);
      ins.push({ name, source: e.source, port });
      body.nodes.push({ id: `arg_${name}`, type: "arg", config: { name }, x: 0, y: 40 + 140 * (ins.length - 1) });
    }
    body.edges.push({ source: `arg_${argFor.get(key)}`, target: e.target, ...(input !== "in" ? { input } : {}) });
  }
  const retFor = new Map<string, string>();
  const maxX = Math.max(...body.nodes.map((n) => n.x ?? 0));
  for (const e of g.edges.filter((x) => inside.has(x.source) && !inside.has(x.target))) {
    const { port, input } = edgePorts(e);
    const key = `${e.source}:${port}`;
    if (!retFor.has(key)) {
      const name = retFor.size ? `out${retFor.size + 1}` : "out";
      retFor.set(key, name);
      body.nodes.push({
        id: `ret_${name}`,
        type: "return",
        config: { name },
        x: maxX + 300,
        y: 40 + 140 * (retFor.size - 1),
      });
      body.edges.push({ source: e.source, target: `ret_${name}`, ...(port !== "out" ? { port } : {}) });
    }
    outs.push({ name: retFor.get(key)!, target: e.target, input, from: key });
  }
  if (!ins.length) {
    // Nothing comes in from outside: give it an input anyway, so it can be wired.
    body.nodes.push({ id: "arg_in", type: "arg", config: { name: "in" }, x: 0, y: 40 });
    ins.push({ name: "in", source: "", port: "out" });
  }
  return { body, ins, outs };
}

/** The selection replaced by one node (a group, or a custom node) wired where the selection was. */
export function replaceSelection(
  g: WfGraph,
  ids: string[],
  folded: Exclude<ReturnType<typeof foldSelection>, string>,
  node: WfNode,
): WfGraph {
  const inside = new Set(ids);
  const nodes = g.nodes.filter((n) => !inside.has(n.id));
  const edges = g.edges.filter((e) => !inside.has(e.source) && !inside.has(e.target));
  for (const i of folded.ins)
    if (i.source)
      edges.push({
        source: i.source,
        target: node.id,
        ...(i.port !== "out" ? { port: i.port } : {}),
        ...(i.name !== "in" ? { input: i.name } : {}),
      });
  for (const o of folded.outs)
    edges.push({
      source: node.id,
      target: o.target,
      ...(o.name !== "out" ? { port: o.name } : {}),
      ...(o.input !== "in" ? { input: o.input } : {}),
    });
  return { nodes: [...nodes, node], edges };
}

/** The settings of a body's nodes that can be made parameters: {node id: [setting, value]}. */
export function paramCandidates(body: WfGraph): { node: string; key: string; value: unknown }[] {
  return body.nodes
    .filter((n) => n.type !== "arg" && n.type !== "return")
    .flatMap((n) =>
      Object.entries(n.config)
        .filter(([k, v]) => k !== "body" && (scalar(v) || Array.isArray(v)))
        .map(([key, value]) => ({ node: n.id, key, value })),
    );
}

export function kindOf(v: unknown): "text" | "number" | "bool" | "json" {
  return typeof v === "number" ? "number" : typeof v === "boolean" ? "bool" : typeof v === "string" ? "text" : "json";
}

/** A one-line summary of a node's settings, for the card. */
export function nodeSummary(
  n: WfNode,
  names: {
    template: (id: number) => string | undefined;
    field: (id: number) => string | undefined;
    custom?: (id: number) => CustomDef | undefined;
  },
) {
  const c = n.config;
  const ref = Object.entries(c).find(([, v]) => paramRef(v));
  switch (n.type) {
    case "llm":
      return c.template != null
        ? (names.template(Number(c.template)) ?? `template #${c.template}`)
        : "Choose a template";
    case "pick":
      return c.path ? `→ ${c.path}` : "Choose a path";
    case "condition":
    case "filter":
      return `${n.type === "filter" ? "keep where " : ""}${c.path ? String(c.path) : "value"} ${OPS[String(c.op)] ?? ""}${c.value != null && c.op !== "exists" && c.op !== "empty" ? ` ${paramRef(c.value) ? `‹${paramRef(c.value)}›` : JSON.stringify(c.value)}` : ""}`;
    case "switch":
      return `${((c.cases as unknown[] | undefined) ?? []).length} cases${c.path ? ` on ${String(c.path)}` : ""}`;
    case "set":
      return ((c.fields as { key: string }[] | undefined) ?? []).map((f) => f.key).join(", ") || "Add fields";
    case "template":
      return (
        String(c.template ?? "")
          .replace(/\s+/g, " ")
          .slice(0, 60) + (c.json ? " · JSON" : "")
      );
    case "for_each":
      return `each of ${c.path ? String(c.path) : "the list"}${c.max_items ? ` · up to ${Number(c.max_items)}` : ""}`;
    case "repeat":
      return `up to ${Number(c.max_rounds ?? 5)} rounds${c.until ? ` · until ${String((c.until as { path?: string }).path ?? "value")} ${OPS[String((c.until as { op?: string }).op)] ?? ""}` : ""}`;
    case "group":
      return `${(c.body as WfGraph | undefined)?.nodes.length ?? 0} nodes`;
    case "custom": {
      const d = names.custom?.(Number(c.node));
      if (!d) return `custom node #${String(c.node)}`;
      const ps = Object.entries((c.params as Record<string, unknown> | undefined) ?? {});
      return `${d.name} · v${String(c.version ?? d.version)}${d.current > Number(c.version ?? d.current) ? " (newer saved)" : ""}${ps.length ? ` · ${ps.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")}` : ""}`;
    }
    case "arg":
    case "return":
      return String(c.name ?? "");
    case "ask_model":
      return (
        String(c.prompt ?? "Write the prompt")
          .replace(/\s+/g, " ")
          .slice(0, 60) + (c.json ? " · JSON" : "")
      );
    case "call_tool":
      return c.tool ? String(c.tool) : "Name the tool";
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
      return c.key
        ? paramRef(c.key)
          ? `outputs.‹${paramRef(c.key)}›`
          : `outputs.${String(c.key)}`
        : "Name the output";
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
      return ref ? `‹${ref[0]} from a parameter›` : (NODES[n.type]?.describe ?? "");
  }
}

/** Where a graph sits: the workflow itself, a loop's body, a group's body, or a custom node's body. */
export type BodyKind = "workflow" | "for_each" | "repeat" | "group" | "custom";

/** Problems per node id ("" for the graph as a whole), as the backend would put them. Bodies are checked too: a
 * problem inside one is shown on the node that holds it. */
export function problems(
  g: WfGraph,
  templateKind: (id: number) => string | undefined,
  scope: Scope = "recording",
  kind: BodyKind = "workflow",
  custom?: (id: number) => CustomDef | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  const by = new Map(g.nodes.map((n) => [n.id, n]));
  const ports = new Map(g.nodes.map((n) => [n.id, portsOf(n, custom)]));
  if (kind === "workflow" && g.nodes.filter((n) => n.type === "input").length !== 1)
    out[""] = `A workflow has exactly one ${infoFor("input", scope)?.label} node.`;
  for (const n of g.nodes) {
    const c = n.config;
    const into = g.edges.filter((e) => e.target === n.id);
    const p = ports.get(n.id)!;
    const missing = p.inputs.filter((i) => !into.some((e) => edgePorts(e).input === i));
    const fail = (m: string) => {
      if (!out[n.id]) out[n.id] = m;
    };
    if (!SCOPE_NODES[scope].includes(n.type))
      fail(
        scope === "tool"
          ? "This node isn’t for tools."
          : scope === "graph"
            ? "This node works on recordings, not the graph."
            : "This node is for graph workflows.",
      );
    if (kind === "workflow" && (n.type === "arg" || n.type === "return"))
      fail("Input and Return nodes belong in a body.");
    if (kind !== "workflow" && n.type === "input") fail("A body starts from Input nodes.");
    if (n.type === "custom" && custom && !custom(Number(c.node)))
      fail("This custom node was removed or isn’t shared with you.");
    if (p.inputs.length && missing.length === p.inputs.length) fail("Connect something into it.");
    else if (missing.length && n.type !== "merge") fail(`Connect its ${missing[0]} input.`);
    for (const i of p.inputs)
      if (!p.many && into.filter((e) => edgePorts(e).input === i).length > 1)
        fail(
          n.type === "set" || p.inputs.length > 1
            ? `Its ${i} input takes one connection.`
            : "It takes one input; join several with a Merge.",
        );
    for (const e of g.edges.filter((x) => x.source === n.id)) {
      const { port, input } = edgePorts(e);
      if (!by.has(e.target)) fail("A connection goes nowhere.");
      else if (!p.outputs.includes(port))
        fail(
          n.type === "condition" ? "Connect its yes or its no." : `It has no ${port} output any more; reconnect it.`,
        );
      else if (!ports.get(e.target)!.inputs.includes(input))
        out[e.target] ||= `It has no ${input} input any more; reconnect it.`;
    }
    if ((n.type === "arg" || n.type === "return") && !NAME_RX.test(String(c.name ?? "")))
      fail("Name it with lowercase letters, digits and _.");
    else if (n.type === "arg" && LOOP_ARGS[kind] && !LOOP_ARGS[kind].includes(String(c.name)))
      fail(`In this loop an input is ${LOOP_ARGS[kind].join(", ")}.`);
    else if (n.type === "return" && LOOP_ARGS[kind] && c.name !== "out") fail("A loop’s body returns out.");
    else if (paramRef(Object.values(c).find((v) => paramRef(v)))) {
      // set by a parameter: checked with its value where the custom node is used
    } else if (n.type === "llm" && c.template == null) fail("Choose a prompt template.");
    else if (n.type === "llm" && templateKind(Number(c.template)) && templateKind(Number(c.template)) !== "prompt")
      fail("Needs a prompt template.");
    else if (n.type === "pick" && !PATH_RX.test(String(c.path ?? "")))
      fail("Give a path like tldr or action_items.0.text.");
    else if ((n.type === "condition" || n.type === "filter") && c.path && !PATH_RX.test(String(c.path)))
      fail("The path is letters, digits, _ and dots.");
    else if (
      (n.type === "condition" || n.type === "filter") &&
      (c.op === "gt" || c.op === "lt") &&
      typeof c.value !== "number"
    )
      fail("Compare with a number.");
    else if (n.type === "switch") {
      const cases = (c.cases as { port?: string; op?: string; value?: unknown }[] | undefined) ?? [];
      const names = cases.map((k) => k.port ?? "");
      if (!cases.length) fail("Add a case.");
      else if (names.some((x) => !NAME_RX.test(x) || x === "default"))
        fail("Name each case in lowercase (not default).");
      else if (new Set(names).size !== names.length) fail("Two cases have the same name.");
      else if (cases.some((k) => (k.op === "gt" || k.op === "lt") && typeof k.value !== "number"))
        fail("Compare with a number.");
    } else if (n.type === "set") {
      const fields = (c.fields as { key?: string; path?: string; template?: string }[] | undefined) ?? [];
      const ins = (c.inputs as string[] | undefined) ?? [];
      if (!fields.length) fail("Add a field.");
      else if (fields.some((f) => !/^[A-Za-z_][A-Za-z0-9_]{0,40}$/.test(f.key ?? "")))
        fail("Each field has a key of letters, digits and _.");
      else if (fields.some((f) => f.path && !PATH_RX.test(f.path)))
        fail("A field’s path is letters, digits, _ and dots.");
      else if (ins.length > 1 && fields.some((f) => f.path != null && !ins.includes(String(f.path).split(".")[0])))
        fail(`Start each path with an input: ${ins.join(", ")}.`);
      else if (ins.some((x) => !NAME_RX.test(x))) fail("Input names are lowercase letters, digits and _.");
    } else if (n.type === "template" && !String(c.template ?? "").trim()) fail("Write the template.");
    else if (n.type === "ask_model" && !String(c.prompt ?? "").trim()) fail("Write its prompt.");
    else if (n.type === "call_tool" && !/^[a-z][a-z0-9_]{1,40}$/.test(String(c.tool ?? "")))
      fail("Name the tool it calls.");
    else if (n.type === "output" && !KEY_RX.test(String(c.key ?? "")))
      fail("Name it: lowercase letters, digits and _, e.g. meeting_notes.");
    else if (n.type === "field" && c.field == null) fail("Choose a custom field.");
    else if (n.type === "candidates" && !between(c.min_confidence, 0, 1, false))
      fail("How sure is a number from 0 to 1.");
    else if (n.type === "candidates" && !between(c.limit, 1, 1000, true)) fail("At most is 1 to 1000 pairs.");
    else if (n.type === "llm_judge" && !between(c.batch, 1, 100, true)) fail("Pairs a call is 1 to 100.");
    else if (n.type === "apply_changes" && !between(c.apply_above, 0, 1, false))
      fail("Sure enough at is a number from 0 to 1.");
    else if (n.type === "apply_changes" && !between(c.max_apply, 0, 1000, true)) fail("At most is 0 to 1000.");
    else if (n.type === "for_each" && !between(c.max_items, 1, 1000, true)) fail("At most is 1 to 1000 items.");
    else if (n.type === "repeat" && !between(c.max_rounds, 1, 50, true)) fail("At most is 1 to 50 rounds.");
    else if (n.type === "extract_rules") {
      for (const pt of (c.patterns as { pattern?: string; type?: string }[] | undefined) ?? []) {
        if (!pt.pattern) fail("A pattern is empty.");
        else if (!TYPE_RX.test(pt.type ?? "")) fail("Each pattern needs a type in capitals, e.g. TICKET.");
        else
          try {
            new RegExp(pt.pattern);
          } catch {
            fail(`Pattern ${pt.pattern} doesn’t work.`);
          }
      }
    }
    if (BODIES.includes(n.type)) {
      const body = c.body as WfGraph | undefined;
      if (!body) fail("It has no body.");
      else {
        const inner = problems(body, templateKind, scope, n.type as BodyKind, custom);
        const first = Object.entries(inner)[0];
        if (first) fail(`Inside: ${first[1]}`);
      }
    }
  }
  if (!out[""] && hasLoop(g))
    out[""] = "The graph loops back on itself; use a For each or Repeat node to go over things again.";
  const outs = bodyPorts(g).outputs;
  if (!out[""] && LOOP_ARGS[kind] && !outs.includes("out"))
    out[""] = "Add a Return node called out: what the body gives back.";
  else if (!out[""] && (kind === "group" || kind === "custom")) {
    if (!bodyPorts(g).inputs.length) out[""] = "Add an Input node, for what comes in.";
    else if (!outs.length && !keeps(g, custom, scope)) out[""] = "Add a Return node, or it gives nothing back.";
  }
  if (kind === "workflow" && !out[""] && !keeps(g, custom, scope))
    out[""] =
      scope === "graph"
        ? "Add an Apply changes node, or the workflow changes nothing."
        : "Add a Save output, Set field or Save entities node, or the workflow keeps nothing.";
  return out;
}

/** Whether a graph keeps something: a saving node here, inside a body, or in a custom node. */
function keeps(g: WfGraph, custom?: (id: number) => CustomDef | undefined, scope: Scope = "recording"): boolean {
  const saving = scope === "graph" ? ["apply_changes"] : KEEPS.filter((t) => t !== "apply_changes");
  return g.nodes.some(
    (n) =>
      saving.includes(n.type) ||
      (BODIES.includes(n.type) && n.config.body ? keeps(n.config.body as WfGraph, custom, scope) : false) ||
      (n.type === "custom" && Boolean(custom?.(Number(n.config.node))?.keeps)),
  );
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

/** The graph as the API takes it: rounded positions, labels only when set, edges with their ports, bodies too. */
export function cleanGraph(g: WfGraph): WfGraph {
  return {
    nodes: g.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      config:
        BODIES.includes(n.type) && n.config.body
          ? { ...n.config, body: cleanGraph(n.config.body as WfGraph) }
          : n.config,
      ...(n.label?.trim() ? { label: n.label.trim() } : {}),
      x: Math.round(n.x ?? 0),
      y: Math.round(n.y ?? 0),
    })),
    edges: g.edges.map((e) => {
      const { port, input } = edgePorts(e);
      return {
        source: e.source,
        target: e.target,
        ...(port !== "out" ? { port } : {}),
        ...(input !== "in" ? { input } : {}),
      };
    }),
  };
}

export function sameGraph(a: WfGraph, b: WfGraph): boolean {
  return JSON.stringify(cleanGraph(a)) === JSON.stringify(cleanGraph(b));
}
