import { rulesText } from "@/components/content-types/model";
import { graphOrder, type PlGraph } from "@/components/pipelines/pipeline-model";
import { cleanGraph, problems, starter, type WfGraph } from "@/components/workflows/workflow-model";

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
