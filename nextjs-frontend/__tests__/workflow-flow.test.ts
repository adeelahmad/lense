import {
  bodyAt,
  cleanGraph,
  defaultConfig,
  foldSelection,
  normalize,
  portsOf,
  problems,
  inScope,
  replaceSelection,
  starterTool,
  withBody,
  type CustomDef,
  type WfGraph,
} from "@/components/workflows/workflow-model";

const kind = (id: number) => ({ 1: "prompt" })[id];

describe("ports", () => {
  it("come from a node's settings: switch cases, set inputs, a group's body, a custom node", () => {
    expect(portsOf({ id: "s", type: "switch", config: { cases: [{ port: "big", op: "exists" }] } }).outputs).toEqual([
      "big",
      "default",
    ]);
    expect(portsOf({ id: "s", type: "set", config: { inputs: ["a", "b"], fields: [] } }).inputs).toEqual(["a", "b"]);
    expect(portsOf({ id: "m", type: "merge", config: {} }).many).toBe(true);
    const body: WfGraph = {
      nodes: [
        { id: "b", type: "arg", config: { name: "second" }, y: 200 },
        { id: "a", type: "arg", config: { name: "first" }, y: 0 },
        { id: "r", type: "return", config: { name: "out" } },
      ],
      edges: [],
    };
    expect(portsOf({ id: "g", type: "group", config: { body } })).toEqual({
      inputs: ["first", "second"],
      outputs: ["out"],
      many: false,
    });
    const d = { id: 4, inputs: ["text"], outputs: ["hit", "miss"] } as CustomDef;
    expect(
      portsOf({ id: "c", type: "custom", config: { node: 4 } }, (id) => (id === 4 ? d : undefined)).outputs,
    ).toEqual(["hit", "miss"]);
  });

  it("read an old condition edge's branch as its port", () => {
    const g = normalize({
      nodes: [],
      edges: [{ source: "c", target: "o", branch: "no" }],
    });
    expect(g.edges).toEqual([{ source: "c", target: "o", port: "no" }]);
    expect(cleanGraph(g).edges).toEqual([{ source: "c", target: "o", port: "no" }]);
  });
});

describe("bodies", () => {
  const loop: WfGraph = {
    nodes: [
      { id: "in", type: "input", config: {} },
      { id: "each", type: "for_each", config: { path: "segments", body: defaultConfig("for_each").body } },
      { id: "o", type: "output", config: { key: "lines" } },
    ],
    edges: [
      { source: "in", target: "each" },
      { source: "each", target: "o" },
    ],
  };

  it("start valid, and are checked where they sit", () => {
    expect(problems(loop, kind)).toEqual({});
    const body = bodyAt(loop, ["each"])!;
    const bad = withBody(loop, ["each"], {
      ...body,
      nodes: body.nodes.map((n) => (n.type === "return" ? { ...n, config: { name: "other" } } : n)),
    });
    expect(problems(bad, kind).each).toMatch(/Inside: .*returns out/);
    expect(problems(bodyAt(bad, ["each"])!, kind, "recording", "for_each")[""]).toMatch(/Return node called out/);
  });

  it("can be made by folding selected nodes, wired where they were", () => {
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "p", type: "pick", config: { path: "summary" } },
        { id: "c", type: "condition", config: { op: "exists" } },
        { id: "o", type: "output", config: { key: "x" } },
      ],
      edges: [
        { source: "in", target: "p" },
        { source: "p", target: "c" },
        { source: "c", target: "o", port: "yes" },
      ],
    };
    const f = foldSelection(g, ["p", "c"]);
    if (typeof f === "string") throw new Error(f);
    expect(f.ins).toEqual([{ name: "in", source: "in", port: "out" }]);
    expect(f.outs).toEqual([{ name: "out", target: "o", input: "in", from: "c:yes" }]);
    expect(problems(f.body, kind, "recording", "group")).toEqual({});
    const out = replaceSelection(g, ["p", "c"], f, { id: "g1", type: "group", config: { body: f.body } });
    expect(out.edges).toEqual([
      { source: "in", target: "g1" },
      { source: "g1", target: "o" },
    ]);
    expect(problems(out, kind)).toEqual({});
    expect(foldSelection(g, ["in", "p"])).toMatch(/start/);
  });
});

describe("tool graphs", () => {
  it("checks an assistant tool drawn on the canvas", () => {
    const g = starterTool();
    expect(problems(g, () => undefined, "tool", "custom")).toEqual({});
    expect(inScope("ask_model", "tool")).toBe(true);
    expect(inScope("extract_rules", "tool")).toBe(false);
    const empty = { ...g, nodes: g.nodes.map((n) => (n.type === "ask_model" ? { ...n, config: { prompt: " " } } : n)) };
    expect(problems(empty, () => undefined, "tool", "custom").ask).toBe("Write its prompt.");
    const call = {
      ...g,
      nodes: g.nodes.map((n) => (n.type === "ask_model" ? { ...n, type: "call_tool", config: { tool: "X" } } : n)),
    };
    expect(problems(call, () => undefined, "tool", "custom").ask).toBe("Name the tool it calls.");
    const recording = { ...g, nodes: [...g.nodes, { id: "keep", type: "save_entities", config: {} }] };
    expect(problems(recording, () => undefined, "tool", "custom").keep).toBe("This node isn’t for tools.");
  });
});
