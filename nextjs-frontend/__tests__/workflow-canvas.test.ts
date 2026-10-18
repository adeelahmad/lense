import { rulesText } from "@/components/content-types/model";
import { graphOrder, type PlGraph } from "@/components/pipelines/pipeline-model";
import {
  cleanGraph,
  effectBadge,
  inScope,
  infoFor,
  nodeSummary,
  portsOf,
  problems,
  starter,
  type ActionDef,
  type WfGraph,
  wouldDo,
} from "@/components/workflows/workflow-model";

const kind = (id: number) => ({ 1: "prompt", 2: "export" })[id];

describe("workflow graphs", () => {
  it("starts with today's entity extraction, and it's valid", () => {
    const g = starter();
    expect(g.nodes.map((n) => n.type)).toEqual(["input", "extract_rules", "save_entities"]);
    expect(problems(g, kind)).toEqual({});
  });

  it("says what the backend would reject", () => {
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "l", type: "llm", config: { template: 2 } },
        { id: "c", type: "condition", config: { op: "gt", value: "x" } },
        { id: "o", type: "output", config: { key: "Bad Key" } },
        { id: "p", type: "pick", config: { path: "a" } },
        { id: "r", type: "extract_rules", config: { patterns: [{ type: "T", pattern: "(" }] } },
      ],
      edges: [
        { source: "in", target: "l" },
        { source: "l", target: "c" },
        { source: "c", target: "o" },
        { source: "in", target: "p" },
        { source: "l", target: "p" },
        { source: "in", target: "r" },
      ],
    };
    const p = problems(g, kind);
    expect(p.l).toMatch(/prompt template/);
    expect(p.c).toMatch(/yes or its no|number/);
    expect(p.o).toMatch(/Name it/);
    expect(p.p).toMatch(/Merge/);
    expect(p.r).toMatch(/doesn’t work/);
  });

  it("finds loops and graphs that keep nothing", () => {
    const loop: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "a", type: "merge", config: {} },
        { id: "b", type: "pick", config: { path: "x" } },
      ],
      edges: [
        { source: "in", target: "a" },
        { source: "a", target: "b" },
        { source: "b", target: "a" },
      ],
    };
    expect(problems(loop, kind)[""]).toMatch(/loop/);
    const idle: WfGraph = { nodes: [loop.nodes[0], loop.nodes[2]], edges: [{ source: "in", target: "b" }] };
    expect(problems(idle, kind)[""]).toMatch(/keeps nothing/);
  });

  it("rounds positions and drops empty labels for the API", () => {
    const g = cleanGraph({ nodes: [{ id: "in", type: "input", config: {}, x: 1.6, y: 2.2, label: " " }], edges: [] });
    expect(g.nodes[0]).toEqual({ id: "in", type: "input", config: {}, x: 2, y: 2 });
  });
});

describe("pipeline graphs", () => {
  it("run each step after the ones it follows, ties left to right", () => {
    const g: PlGraph = {
      nodes: [
        { id: "wf", step: { type: "workflow", workflow: 1 }, x: 500 },
        { id: "sum", step: { type: "summarize" }, x: 250 },
        { id: "an", step: { type: "analyze" }, x: 0 },
        { id: "rep", step: { type: "report" }, x: 100, y: 200 },
      ],
      edges: [
        { source: "an", target: "wf" },
        { source: "an", target: "sum" },
        { source: "sum", target: "wf" },
      ],
    };
    expect(graphOrder(g)?.map((n) => n.id)).toEqual(["an", "rep", "sum", "wf"]);
    expect(graphOrder({ ...g, edges: [...g.edges, { source: "wf", target: "an" }] })).toBeNull();
  });
});

describe("content types", () => {
  it("say how a type is recognised", () => {
    expect(rulesText({ extensions: [".srt", ".vtt"], pattern: "sync", min_minutes: 5 })).toBe(
      ".srt .vtt · name ~ sync · ≥ 5 min",
    );
    expect(rulesText({ forms: ["chat"] })).toBe("holds a chat");
    expect(rulesText({ extensions: [".json"], forms: ["records"] })).toBe(".json · holds records");
    expect(rulesText(null)).toBe("");
  });
});

// as GET /api/v1/automations/actions lists them
const actions: ActionDef[] = [
  {
    name: "notify",
    label: "Send a message",
    group: "Messages",
    params: { type: "object", properties: { to: { type: "string" }, text: { type: "string", title: "Message" } } },
    required: ["text"],
    effect: "depends",
  },
  {
    name: "http_request",
    label: "Call a web address",
    group: "Web",
    params: { type: "object", properties: { url: { type: "string", title: "Address" } } },
    required: ["url"],
    effect: "out",
  },
  { name: "log_line", label: "Write to the run log", group: "Run", params: {}, required: [], effect: "read" },
];
const names = { template: () => undefined, field: () => undefined, actions };

describe("automation workflows (scope event)", () => {
  it("start from what happened with a message to you, and that's valid", () => {
    const g = starter("event");
    expect(g.nodes.map((n) => n.type)).toEqual(["input", "action"]);
    expect(problems(g, kind, "event", "workflow", undefined, actions)).toEqual({});
    expect(infoFor("input", "event")?.label).toBe("What happened");
    expect(nodeSummary(g.nodes[1], names)).toBe("Send a message: {{ event.summary }}");
  });

  it("have action and decide nodes, which other scopes don't", () => {
    expect(inScope("action", "event")).toBe(true);
    expect(inScope("decide", "event")).toBe(true);
    expect(inScope("extract_rules", "event")).toBe(false);
    expect(inScope("action", "recording")).toBe(false);
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "a", type: "action", config: { action: "log_line", params: { text: "hi" } } },
      ],
      edges: [{ source: "in", target: "a" }],
    };
    expect(problems(g, kind, "recording")).toMatchObject({ a: "This node is for automations." });
    expect(problems(starter(), kind, "event")).toMatchObject({ rules: "This node isn’t for automations." });
  });

  it("give a decide node a port per option, then unsure", () => {
    const n = { id: "d", type: "decide", config: { question: "Today?", options: { urgent: "now", later: "wait" } } };
    expect(portsOf(n)).toEqual({ inputs: ["in"], outputs: ["urgent", "later", "unsure"], many: false });
    expect(nodeSummary(n, names)).toBe("Today? · urgent / later");
  });

  it("say what the backend would reject in their settings", () => {
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "none", type: "action", config: { params: {} } },
        { id: "bare", type: "action", config: { action: "notify", params: {} } },
        {
          id: "both",
          type: "action",
          config: { action: "log_line", params: {}, delay: "5m", when: "2026-10-18T07:00:00Z" },
        },
        { id: "slow", type: "action", config: { action: "log_line", params: {}, delay: "soon" } },
        { id: "q", type: "decide", config: { question: "", options: { yes: "a", no: "b" } } },
        { id: "few", type: "decide", config: { question: "Ok?", options: { yes: "a" } } },
        { id: "bad", type: "decide", config: { question: "Ok?", options: { yes: "a", unsure: "b" } } },
        { id: "mean", type: "decide", config: { question: "Ok?", options: { yes: "a", no: "" } } },
        { id: "sure", type: "decide", config: { question: "Ok?", options: { yes: "a", no: "b" }, min_confidence: 2 } },
      ],
      edges: ["none", "bare", "both", "slow", "q", "few", "bad", "mean", "sure"].map((t) => ({
        source: "in",
        target: t,
      })),
    };
    expect(problems(g, kind, "event", "workflow", undefined, actions)).toEqual({
      none: "Choose an action.",
      bare: "Fill in Message.",
      both: "Wait for a delay or until a time, not both.",
      slow: "A wait is a whole number and s, m, h or d, like 30m.",
      q: "Ask its question.",
      few: "Give 2 to 10 options.",
      bad: "Name each option in lowercase letters, digits and _ (not unsure).",
      mean: "Say what each option means.",
      sure: "How sure is a number from 0 to 1.",
    });
  });

  it("keep the leaf rule: nothing after an action that reaches outside Lens or waits", () => {
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "web", type: "action", config: { action: "http_request", params: { url: "https://example.org" } } },
        { id: "later", type: "action", config: { action: "log_line", params: { text: "x" }, delay: "30m" } },
        { id: "log", type: "action", config: { action: "log_line", params: { text: "y" } } },
        { id: "end", type: "action", config: { action: "log_line", params: { text: "z" } } },
      ],
      edges: [
        { source: "in", target: "web" },
        { source: "web", target: "end" },
        { source: "in", target: "later" },
        { source: "later", target: "log" },
      ],
    };
    const p = problems(g, kind, "event", "workflow", undefined, actions);
    expect(p.web).toBe(
      "It reaches outside Lens, so it asks you first and passes nothing on: connect nothing after it.",
    );
    expect(p.later).toBe("It waits before it acts, so it passes nothing on: connect nothing after it.");
    expect(p.log).toBeUndefined();
    expect(problems({ nodes: [g.nodes[0]], edges: [] }, kind, "event")).toEqual({
      "": "Add an action, or the automation does nothing.",
    });
  });

  it("say whether an action runs on its own, and what a try would do", () => {
    expect(effectBadge("read").label).toBe("Runs on its own");
    expect(effectBadge("out").label).toBe("Asks you first");
    expect(wouldDo({ node: "web", action: "http_request", status: "waiting", untrusted: true }, actions)).toBe(
      "web · Call a web address: would ask you first (reads outside text)",
    );
    expect(wouldDo({ node: "log", action: "log_line", status: "done" }, actions)).toBe(
      "log · Write to the run log: would run at once",
    );
  });
});
