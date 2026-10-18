/**
 * An automation as the studio's builder edits it: When (triggers, each with "only when" rows, and a join line), If
 * (rows over the run context) and Then (an ordered list of action steps), read from and written back to the API's
 * shape (docs/automations.md, Saving automations). A Then that isn't a straight list is kept as its graph and edited
 * on the canvas.
 */
import type { Tone } from "@/components/ui/badge";
import { effectBadge, type ActionDef, type WfEdge, type WfGraph } from "@/components/workflows/workflow-model";

/** One test of a match tree: {path, op, value}. */
export type Test = { path: string; op: string; value?: unknown };
/** A flat match tree, as rows: all (or any) of the tests. */
export type Rows = { mode: "all" | "any"; rows: Test[] };
export type Trigger = {
  as: string;
  on: string;
  kinds?: string[];
  where?: unknown;
  cron?: string;
  timezone?: string;
  at?: string;
  field?: string;
  offset?: string;
  [key: string]: unknown;
};
export type Join = {
  mode: "any" | "all" | "count" | "absent" | "batch";
  within?: string;
  key?: string;
  of?: string;
  at_least?: number;
  after?: string;
  missing?: string;
};
/** One action of the Then, in order: an action node. */
export type Step = { id: string; action: string; params: Record<string, unknown>; delay?: string; when?: string };
export type Draft = {
  name: string;
  triggers: Trigger[];
  join: Join;
  /** The If as a match tree, or null for none. */
  if: unknown;
  /** The Then as steps, or null when it branches (then `graph` is what it is). */
  steps: Step[] | null;
  graph: WfGraph;
  spaces?: number[] | null;
};

// ---------- match trees as rows ----------
const isTest = (m: unknown): m is Test =>
  Boolean(m) && typeof m === "object" && typeof (m as Test).op === "string" && !("seen" in (m as object));

/** A tree as rows, or null when it nests (it is shown in words and edited as a whole, not as rows). */
export function rowsOf(tree: unknown): Rows | null {
  if (tree == null) return { mode: "all", rows: [] };
  if (isTest(tree)) return { mode: "all", rows: [{ ...tree, path: tree.path ?? "" }] };
  const t = tree as { all?: unknown[]; any?: unknown[] };
  const list = t.all ?? t.any;
  if (!Array.isArray(list) || Object.keys(t).length !== 1 || !list.every(isTest)) return null;
  return { mode: t.all ? "all" : "any", rows: list.map((x) => ({ ...(x as Test), path: (x as Test).path ?? "" })) };
}

/** Rows as a tree: rows without a path or a test are left out; none gives no tree. */
export function treeOf(r: Rows): unknown {
  const rows = r.rows
    .filter((x) => x.path.trim() && x.op)
    .map((x) => (NO_VALUE.includes(x.op) ? { path: x.path.trim(), op: x.op } : { ...x, path: x.path.trim() }));
  return rows.length ? { [r.mode]: rows } : undefined;
}

export const OP_LABEL: Record<string, string> = {
  contains: "contains",
  equals: "is",
  not_equals: "isn’t",
  starts_with: "starts with",
  ends_with: "ends with",
  matches: "matches",
  in: "is one of",
  not_in: "isn’t one of",
  gt: "is more than",
  lt: "is less than",
  between: "is between",
  before: "is before",
  after: "is after",
  within: "is within",
  weekday: "is on",
  time_between: "is between the times",
  exists: "is there",
  empty: "is empty",
  changed: "changed",
};
export const NO_VALUE = ["exists", "empty", "changed"];
/** Tests that take a list of values, written with commas. */
const LISTS = ["in", "not_in", "between", "weekday", "time_between"];

export type FieldType = "text" | "number" | "time" | "list" | "bool" | "any";

/** The tests that suit a field of this type, the likeliest first. */
export function opsFor(type: FieldType): string[] {
  switch (type) {
    case "text":
      return [
        "contains",
        "equals",
        "not_equals",
        "starts_with",
        "ends_with",
        "matches",
        "in",
        "not_in",
        "exists",
        "empty",
        "changed",
      ];
    case "number":
      return ["gt", "lt", "equals", "not_equals", "between", "in", "exists", "changed"];
    case "time":
      return ["within", "before", "after", "between", "weekday", "time_between", "exists"];
    case "list":
      return ["contains", "empty", "exists", "in", "not_in"];
    case "bool":
      return ["equals", "exists", "changed"];
    default:
      return Object.keys(OP_LABEL);
  }
}

/** A test's value as its box shows it. */
export function valueText(t: Test): string {
  if (t.value == null) return "";
  if (Array.isArray(t.value)) return t.value.map(String).join(", ");
  return String(t.value);
}

/** What's typed in a test's box as its value: lists with commas, numbers for number tests, true/false for toggles. */
export function valueFrom(op: string, text: string, type: FieldType = "any"): unknown {
  if (NO_VALUE.includes(op)) return undefined;
  const num = (s: string) => (/^-?\d+(\.\d+)?$/.test(s.trim()) ? Number(s.trim()) : s.trim());
  if (LISTS.includes(op))
    return text
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean)
      .map((x) => (type === "number" || op === "between" ? num(x) : x));
  if (op === "gt" || op === "lt" || type === "number") return num(text);
  if (type === "bool" && (text === "true" || text === "false")) return text === "true";
  return text;
}

/** A kind's fields a trigger's "only when" can test, from its schema: {path, label, type}, nested one level. */
export function kindFields(schema: unknown): { path: string; label: string; type: FieldType }[] {
  const out: { path: string; label: string; type: FieldType }[] = [];
  const walk = (props: Record<string, Prop> | undefined, prefix: string, above: string) => {
    for (const [k, p] of Object.entries(props ?? {})) {
      const path = prefix ? `${prefix}.${k}` : k;
      const label = above ? `${above} · ${p.title ?? k}` : (p.title ?? k);
      const type = typeOf(p);
      if (type === "object") {
        if (!prefix) walk(p.properties, path, p.title ?? k);
      } else if (!["id", "kind", "space", "tag"].includes(path)) out.push({ path, label, type });
    }
  };
  walk((schema as { properties?: Record<string, Prop> })?.properties, "", "");
  return out;
}
type Prop = { type?: string | string[]; title?: string; format?: string; properties?: Record<string, Prop> };
function typeOf(p: Prop): FieldType | "object" {
  const t = Array.isArray(p.type) ? p.type.find((x) => x !== "null") : p.type;
  if (t === "object") return "object";
  if (t === "array") return "list";
  if (t === "integer" || t === "number") return "number";
  if (t === "boolean") return "bool";
  if (t === "string") return p.format === "date-time" || p.format === "date" ? "time" : "text";
  return "any";
}

// ---------- triggers and the join ----------
/** Trigger cards that aren't a kind of activity, as "Add a trigger" offers them. */
export const CLOCK: { on: string; label: string; hint: string }[] = [
  { on: "schedule", label: "Every day at…", hint: "On a schedule, like every weekday at 07:00" },
  { on: "at", label: "At a time", hint: "Once, at a date and time" },
  { on: "webhook", label: "A webhook is called", hint: "Its own address, given when it is saved" },
  { on: "manual", label: "When I run it", hint: "Only when you press Run now" },
];

/** A name for a new trigger, from its kind (email.received → email) or what it is, not taken yet. */
export function aliasFor(what: string, taken: string[]): string {
  const base =
    (what.includes(".") ? what.split(".")[0] : ({ schedule: "clock", at: "time", webhook: "hook" }[what] ?? what))
      .toLowerCase()
      .replace(/[^a-z0-9_]/g, "_")
      .replace(/^[^a-z]+/, "") || "trigger";
  let name = base;
  for (let k = 2; taken.includes(name); k++) name = `${base}${k}`;
  return name;
}

/** A new trigger: on a kind of activity, or a clock, webhook or manual one. */
export function newTrigger(what: string, taken: string[], timezone = "UTC"): Trigger {
  const as = aliasFor(what, taken);
  if (what === "schedule") return { as, on: "schedule", cron: "0 7 * * *", timezone };
  if (what === "at") return { as, on: "at", at: "", timezone };
  if (what === "webhook" || what === "manual") return { as, on: what };
  return { as, on: "event", kinds: [what] };
}

/** The join line in words, as the builder's choice reads it. */
export function joinText(j: Join): string {
  const within = j.within ?? "10m";
  const same = j.key ? ` for the same ${j.key}` : "";
  switch (j.mode) {
    case "all":
      return `All of these within ${within}${same}`;
    case "count":
      return `${j.at_least ?? 3} times ${j.of ?? "the first"} within ${within}${same}`;
    case "absent":
      return `${j.after ?? "the first"} and then no ${j.missing ?? "the second"} within ${within}${same}`;
    case "batch":
      return `Collect for ${within}, then once`;
    default:
      return "Any of these";
  }
}

/** A join of this mode with the triggers' names filled in. */
export function joinFor(mode: Join["mode"], triggers: Trigger[], was?: Join): Join {
  const [a, b] = triggers.map((t) => t.as);
  const within = was?.within ?? (mode === "absent" ? "2d" : mode === "batch" ? "1h" : mode === "count" ? "5m" : "10m");
  if (mode === "any") return { mode };
  if (mode === "all") return { mode, within, ...(was?.key ? { key: was.key } : {}) };
  if (mode === "count") return { mode, of: was?.of ?? a, at_least: was?.at_least ?? 3, within };
  if (mode === "absent") return { mode, after: was?.after ?? a, missing: was?.missing ?? b ?? a, within };
  return { mode, within };
}

// ---------- the Then: steps and the graph ----------
const params = (c: Record<string, unknown>) => (c.params as Record<string, unknown> | undefined) ?? {};

/** An action that passes nothing on: it reaches outside Lens or other people (or may: depends), or it waits. Such a
 * step hangs off the one before it, as the leaf rule has it. */
export function passesNothing(s: Step, actions?: ActionDef[]): boolean {
  if (s.delay || s.when) return true;
  const e = actions?.find((a) => a.name === s.action)?.effect;
  return e === undefined || e === "out" || e === "depends";
}

/** Steps as an event workflow: What happened, then each action after the last one that passes something on. */
export function graphOf(steps: Step[], actions?: ActionDef[]): WfGraph {
  const nodes: WfGraph["nodes"] = [{ id: "in", type: "input", config: {}, x: 0, y: 120 }];
  const edges: WfEdge[] = [];
  let from = "in";
  steps.forEach((s, k) => {
    const config: Record<string, unknown> = { action: s.action, params: s.params };
    if (s.delay) config.delay = s.delay;
    if (s.when) config.when = s.when;
    nodes.push({ id: s.id, type: "action", config, x: 280 * (k + 1), y: 120 });
    edges.push({ source: from, target: s.id });
    if (!passesNothing(s, actions)) from = s.id;
  });
  return { nodes, edges };
}

/** A graph as steps, or null when it branches (a decide, condition or loop node, or actions side by side that a list
 * wouldn't draw the same way). */
export function stepsOf(g: WfGraph | null | undefined, actions?: ActionDef[]): Step[] | null {
  if (!g) return [];
  const input = g.nodes.filter((n) => n.type === "input");
  if (input.length !== 1 || g.nodes.some((n) => n.type !== "input" && n.type !== "action")) return null;
  if (g.edges.some((e) => (e.port && e.port !== "out") || (e.input && e.input !== "in") || e.branch)) return null;
  const by = new Map(g.nodes.map((n) => [n.id, n]));
  const kids = (id: string) =>
    g.edges
      .filter((e) => e.source === id)
      .map((e) => by.get(e.target))
      .filter((n) => n != null);
  const out: Step[] = [];
  let cur = input[0].id;
  for (let guard = 0; guard <= g.nodes.length; guard++) {
    const next = kids(cur);
    const ends = next
      .filter((n) => !kids(n.id).length)
      .sort((a, b) => (a.x ?? 0) - (b.x ?? 0) || a.id.localeCompare(b.id));
    const on = next.filter((n) => kids(n.id).length);
    if (on.length > 1) return null;
    for (const n of ends) out.push(stepFrom(n));
    if (!on.length) break;
    out.push(stepFrom(on[0]));
    cur = on[0].id;
  }
  if (out.length !== g.nodes.length - 1) return null;
  // a list draws it this way only if it is the graph itself
  const again = graphOf(out, actions)
    .edges.map((e) => `${e.source}>${e.target}`)
    .sort();
  const was = g.edges.map((e) => `${e.source}>${e.target}`).sort();
  return again.join() === was.join() ? out : null;
}

function stepFrom(n: WfGraph["nodes"][number]): Step {
  const c = n.config;
  return {
    id: n.id,
    action: String(c.action ?? ""),
    params: params(c),
    ...(c.delay != null ? { delay: String(c.delay) } : {}),
    ...(c.when != null ? { when: String(c.when) } : {}),
  };
}

/** An id for a new step, not taken yet. */
export function stepId(steps: Step[], action: string): string {
  const base = action.split("_")[0] || "a";
  const taken = new Set(steps.map((s) => s.id));
  let id = base;
  for (let k = 2; taken.has(id); k++) id = `${base}${k}`;
  return id;
}

// ---------- the API's shape ----------
type ApiAutomation = {
  name: string;
  triggers: Record<string, unknown>[];
  join?: Record<string, unknown> | null;
  if?: unknown;
  graph?: Record<string, unknown> | null;
  spaces?: number[] | null;
};

/** An automation from the API as a draft for the builder. */
export function fromApi(a: ApiAutomation, actions?: ActionDef[]): Draft {
  const graph = (a.graph as WfGraph | null | undefined) ?? { nodes: [], edges: [] };
  return {
    name: a.name,
    triggers: (a.triggers ?? []) as Trigger[],
    join: ((a.join as Join | null | undefined) ?? { mode: "any" }) as Join,
    if: a.if ?? null,
    steps: stepsOf(graph, actions),
    graph,
    spaces: a.spaces,
  };
}

/** A draft as POST /automations (and PATCH) take it. */
export function toApi(d: Draft, actions?: ActionDef[]) {
  const triggers = d.triggers.map((t) => {
    const out: Record<string, unknown> = { ...t };
    if (out.where === undefined || out.where === null) delete out.where;
    return out;
  });
  return {
    name: d.name.trim() || suggestedName(d),
    triggers,
    join: triggers.length > 1 ? d.join : { mode: "any" },
    if: d.if ?? null,
    graph: d.steps ? graphOf(d.steps, actions) : d.graph,
  };
}

/** A name for an automation nobody named: its first action and trigger. */
export function suggestedName(
  d: Draft,
  labels?: { kind?: (k: string) => string; action?: (a: string) => string },
): string {
  const t = d.triggers[0];
  const when = !t
    ? ""
    : t.on === "event"
      ? (labels?.kind?.(t.kinds?.[0] ?? "") ?? t.kinds?.[0] ?? "")
      : (CLOCK.find((c) => c.on === t.on)?.label ?? t.on);
  const first = d.steps?.[0];
  const what = first ? (labels?.action?.(first.action) ?? first.action) : "";
  return [what, when && `on ${when.toLowerCase()}`].filter(Boolean).join(" ") || "New automation";
}

/** What a step's badge says: whether it runs on its own or asks first, with the person's pick for it. */
export function stepBadge(s: Step, actions: ActionDef[] | undefined, policy?: Record<string, string>) {
  if (policy?.[s.id] === "auto") return { label: "Runs on its own", tone: "green" as const };
  return effectBadge(actions?.find((a) => a.name === s.action)?.effect);
}

/** Whether a step's action can be let run on its own: one that asks first because it reaches outside Lens or other
 * people (a change inside Lens asks unless it is to what set it off, whatever the pick). */
export const canRunOnItsOwn = (s: Step, actions?: ActionDef[]) =>
  ["out", "depends"].includes(actions?.find((a) => a.name === s.action)?.effect ?? "");

/** What's missing before saving, by where it is ("" for the automation): the API names the rest. */
export function draftProblems(d: Draft, actions?: ActionDef[]): Record<string, string> {
  const out: Record<string, string> = {};
  if (!d.triggers.length) out.triggers = "Add a trigger: what sets it off.";
  d.triggers.forEach((t, k) => {
    if (t.on === "event" && !t.kinds?.length) out[`trigger.${k}`] = "Choose what happens.";
    if (t.on === "schedule" && !String(t.cron ?? "").trim()) out[`trigger.${k}`] = "Give the schedule.";
    if (t.on === "at" && !String(t.at ?? "").trim()) out[`trigger.${k}`] = "Give the date and time.";
  });
  if (d.steps) {
    if (!d.steps.length) out.steps = "Add an action: what it does.";
    d.steps.forEach((s) => {
      const a = actions?.find((x) => x.name === s.action);
      const missing = a?.required?.find((r) => s.params[r] == null || s.params[r] === "");
      if (!a) out[`step.${s.id}`] = "Choose an action.";
      else if (missing) {
        const title = ((a.params.properties ?? {}) as Record<string, { title?: string }>)[missing]?.title ?? missing;
        out[`step.${s.id}`] = `Fill in ${title}.`;
      }
    });
  }
  return out;
}

/** A run's status, as its badge says it. */
export function runStatus(s: string | null | undefined): { label: string; tone: Tone } {
  const known: Record<string, { label: string; tone: Tone }> = {
    done: { label: "Done", tone: "green" },
    partly: { label: "Partly done", tone: "gate" },
    failed: { label: "Failed", tone: "red" },
    running: { label: "Running", tone: "intent" },
    waiting: { label: "Waiting", tone: "gate" },
    held: { label: "Held: over a budget", tone: "gate" },
    released: { label: "Let through", tone: "neutral" },
    skipped: { label: "Skipped", tone: "neutral" },
  };
  return known[s ?? ""] ?? { label: s ?? "—", tone: "neutral" };
}
