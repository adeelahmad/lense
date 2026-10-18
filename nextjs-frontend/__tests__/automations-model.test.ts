import {
  aliasFor,
  draftProblems,
  fromApi,
  graphOf,
  joinFor,
  joinText,
  kindFields,
  newTrigger,
  opsFor,
  rowsOf,
  stepBadge,
  stepsOf,
  suggestedName,
  toApi,
  treeOf,
  valueFrom,
  valueText,
  type Draft,
  type Step,
} from "@/components/automations/model";
import type { ActionDef } from "@/components/workflows/workflow-model";

// as GET /api/v1/automations/actions lists them
const actions: ActionDef[] = [
  { name: "log_line", label: "Write to the run log", group: "Run", params: {}, required: ["text"], effect: "read" },
  {
    name: "ask_model",
    label: "Ask the model",
    group: "AI",
    params: { type: "object", properties: { prompt: { type: "string", title: "Prompt" } } },
    required: ["prompt"],
    effect: "read",
  },
  { name: "notify", label: "Send a message", group: "Messages", params: {}, required: ["text"], effect: "depends" },
  { name: "http_request", label: "Call a web address", group: "Web", params: {}, required: ["url"], effect: "out" },
];
const step = (id: string, action: string, extra: Partial<Step> = {}): Step => ({ id, action, params: {}, ...extra });

describe("the builder's rows", () => {
  it("reads a flat tree as rows and writes them back", () => {
    const tree = {
      any: [
        { path: "object.from.0", op: "ends_with", value: "@stmarys.org" },
        { path: "summary", op: "exists" },
      ],
    };
    const r = rowsOf(tree)!;
    expect(r).toEqual({ mode: "any", rows: tree.any });
    expect(treeOf(r)).toEqual(tree);
    expect(rowsOf(undefined)).toEqual({ mode: "all", rows: [] });
    expect(rowsOf({ path: "summary", op: "exists" })).toEqual({
      mode: "all",
      rows: [{ path: "summary", op: "exists" }],
    });
    expect(treeOf({ mode: "all", rows: [{ path: " ", op: "contains", value: "x" }] })).toBeUndefined();
    expect(treeOf({ mode: "all", rows: [{ path: "summary", op: "exists", value: "left over" }] })).toEqual({
      all: [{ path: "summary", op: "exists" }],
    });
  });

  it("leaves a nested tree to be shown in words", () => {
    expect(rowsOf({ all: [{ any: [{ path: "a", op: "exists" }] }] })).toBeNull();
    expect(rowsOf({ not: { path: "a", op: "exists" } })).toBeNull();
    expect(rowsOf({ all: [{ seen: { kinds: ["email.received"], within: "2d" } }] })).toBeNull();
  });

  it("offers the tests that suit a field, and reads typed values by test", () => {
    expect(opsFor("text")[0]).toBe("contains");
    expect(opsFor("number")[0]).toBe("gt");
    expect(opsFor("time")).toContain("weekday");
    expect(valueFrom("in", "a@x.io, b@x.io")).toEqual(["a@x.io", "b@x.io"]);
    expect(valueFrom("between", "1, 5")).toEqual([1, 5]);
    expect(valueFrom("gt", "3")).toBe(3);
    expect(valueFrom("equals", "3", "number")).toBe(3);
    expect(valueFrom("equals", "3")).toBe("3");
    expect(valueFrom("exists", "x")).toBeUndefined();
    expect(valueText({ path: "a", op: "in", value: ["x", "y"] })).toBe("x, y");
  });

  it("lists a kind's fields from its schema, nested one level, with their types", () => {
    const schema = {
      type: "object",
      properties: {
        id: { type: "string" },
        summary: { type: "string", title: "Summary" },
        published: { type: "string", format: "date-time", title: "When" },
        object: {
          type: "object",
          title: "What",
          properties: {
            from: { type: "array", title: "Sender" },
            attachments: { type: "integer", title: "Attachments" },
          },
        },
      },
    };
    expect(kindFields(schema)).toEqual([
      { path: "summary", label: "Summary", type: "text" },
      { path: "published", label: "When", type: "time" },
      { path: "object.from", label: "What · Sender", type: "list" },
      { path: "object.attachments", label: "What · Attachments", type: "number" },
    ]);
  });
});

describe("triggers and the join line", () => {
  it("names new triggers after what they are, once each", () => {
    expect(aliasFor("email.received", [])).toBe("email");
    expect(aliasFor("email.received", ["email"])).toBe("email2");
    expect(newTrigger("schedule", [], "Europe/London")).toEqual({
      as: "clock",
      on: "schedule",
      cron: "0 7 * * *",
      timezone: "Europe/London",
    });
    expect(newTrigger("sensor.alert", [])).toEqual({ as: "sensor", on: "event", kinds: ["sensor.alert"] });
    expect(newTrigger("webhook", [])).toEqual({ as: "hook", on: "webhook" });
  });

  it("reads each join as its line says it", () => {
    const ts = [newTrigger("email.received", []), newTrigger("calendar_event.added", ["email"])];
    expect(joinText(joinFor("any", ts))).toBe("Any of these");
    expect(joinText({ ...joinFor("all", ts), key: "object.from.0" })).toBe(
      "All of these within 10m for the same object.from.0",
    );
    expect(joinText(joinFor("count", ts))).toBe("3 times email within 5m");
    expect(joinText(joinFor("absent", ts))).toBe("email and then no calendar_event within 2d");
    expect(joinText(joinFor("batch", ts))).toBe("Collect for 1h, then once");
    expect(joinFor("count", ts, { mode: "all", within: "1h" })).toEqual({
      mode: "count",
      of: "email",
      at_least: 3,
      within: "1h",
    });
  });
});

describe("the Then as a list", () => {
  it("hangs what passes nothing on off the step before it, and reads the graph back as the list", () => {
    const steps = [
      step("ask", "ask_model"),
      step("web", "http_request"),
      step("tell", "notify"),
      step("log", "log_line", { delay: "30m" }),
      step("note", "log_line"),
    ];
    const g = graphOf(steps, actions);
    expect(g.edges).toEqual([
      { source: "in", target: "ask" },
      { source: "ask", target: "web" },
      { source: "ask", target: "tell" },
      { source: "ask", target: "log" },
      { source: "ask", target: "note" },
    ]);
    expect(g.nodes[4].config).toEqual({ action: "log_line", params: {}, delay: "30m" });
    expect(stepsOf(g, actions)).toEqual(steps);
  });

  it("says a graph branches when a list can't draw it", () => {
    const decide = graphOf([step("a", "log_line")], actions);
    decide.nodes.push({ id: "d", type: "decide", config: {} });
    expect(stepsOf(decide, actions)).toBeNull();
    const side = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "a", type: "action", config: { action: "log_line" } },
        { id: "b", type: "action", config: { action: "log_line" } },
      ],
      edges: [
        { source: "in", target: "a" },
        { source: "in", target: "b" },
      ],
    };
    expect(stepsOf(side, actions)).toBeNull();
    expect(stepsOf(null)).toEqual([]);
  });

  it("goes to the API's shape and back", () => {
    const api = {
      name: "School emails",
      triggers: [{ as: "mail", on: "event", kinds: ["email.received"] }],
      join: { mode: "any" },
      if: { all: [{ path: "event.object.subject", op: "contains", value: "trip" }] },
      graph: graphOf([step("a", "log_line", { params: { text: "{{ event.summary }}" } })], actions),
      spaces: [3],
    };
    const d = fromApi(api, actions);
    expect(d.steps).toEqual([{ id: "a", action: "log_line", params: { text: "{{ event.summary }}" } }]);
    expect(toApi(d, actions)).toEqual({
      name: "School emails",
      triggers: api.triggers,
      join: { mode: "any" },
      if: api.if,
      graph: api.graph,
    });
    // a join only goes with several triggers; a branching graph goes as it is
    const two: Draft = {
      ...d,
      triggers: [...d.triggers, { as: "cal", on: "event", kinds: ["calendar_event.added"] }],
      join: { mode: "all", within: "1d" },
      steps: null,
      graph: { nodes: [], edges: [] },
    };
    expect(toApi(two, actions)).toMatchObject({ join: { mode: "all", within: "1d" }, graph: { nodes: [], edges: [] } });
    expect(toApi({ ...d, name: " ", triggers: [] }, actions).name).toBe("log_line");
  });

  it("names an unnamed automation, says what is missing, and badges each step's risk", () => {
    const d: Draft = {
      name: "",
      triggers: [{ as: "mail", on: "event", kinds: [] }],
      join: { mode: "any" },
      if: null,
      steps: [step("a", "ask_model"), step("b", "")],
      graph: { nodes: [], edges: [] },
    };
    expect(draftProblems(d, actions)).toEqual({
      "trigger.0": "Choose what happens.",
      "step.a": "Fill in Prompt.",
      "step.b": "Choose an action.",
    });
    expect(draftProblems({ ...d, triggers: [], steps: [] }, actions)).toEqual({
      triggers: "Add a trigger: what sets it off.",
      steps: "Add an action: what it does.",
    });
    expect(
      suggestedName(
        { ...d, triggers: [{ as: "m", on: "event", kinds: ["email.received"] }] },
        { kind: () => "Email received", action: () => "Ask the model" },
      ),
    ).toBe("Ask the model on email received");
    expect(stepBadge(step("w", "http_request"), actions).label).toBe("Asks you first");
    expect(stepBadge(step("w", "http_request"), actions, { w: "auto" }).label).toBe("Runs on its own");
  });
});
