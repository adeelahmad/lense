import {
  actionProblem,
  actionText,
  cleanActions,
  presetOf,
  resultText,
  routineText,
  scheduleFor,
  took,
  touchesGraph,
  type Names,
  type RoutineAction,
} from "@/components/routines/routine-model";
import { inScope, nodeSummary, problems, starter, type WfGraph } from "@/components/workflows/workflow-model";

const names: Names = {
  watch: (id) => ({ 1: "Dropbox: /Inbox" })[id],
  pipeline: (id) => ({ 7: "Interviews" })[id],
  workflow: (id) =>
    ({ 3: { name: "Organise the entity graph", scope: "graph" }, 4: { name: "Meeting notes", scope: "recording" } })[
      id
    ],
};

describe("routine schedules", () => {
  it("maps presets to cron and back", () => {
    expect(scheduleFor("hourly", "")).toBe("0 * * * *");
    expect(scheduleFor("daily", "")).toBe("0 3 * * *");
    expect(scheduleFor("weekdays", "")).toBe("0 9 * * 1-5");
    expect(scheduleFor("manual", "0 3 * * *")).toBeNull();
    expect(scheduleFor("custom", "  30  2 * * * ")).toBe("30 2 * * *");
    expect(scheduleFor("custom", " ")).toBeNull();
    expect(presetOf("0 3 * * *")).toBe("daily");
    expect(presetOf("0  9 * * 1-5")).toBe("weekdays");
    expect(presetOf("@hourly")).toBe("hourly");
    expect(presetOf("15 4 * * *")).toBe("custom");
    expect(presetOf(null)).toBe("manual");
  });
});

describe("routine actions", () => {
  const actions: RoutineAction[] = [
    { type: "sync" },
    { type: "pipeline", recordings: "unprocessed" },
    { type: "workflow", workflow: 3, propose_only: true },
  ];

  it("says what a routine does in plain words", () => {
    expect(actionText({ type: "sync", watches: [1] }, names)).toBe("Sync Dropbox: /Inbox");
    expect(actionText({ type: "pipeline", pipeline: 7 }, names)).toBe(
      "Run Interviews on new recordings (since the last run)",
    );
    expect(actionText({ type: "workflow", workflow: 4, recordings: "all" }, names)).toBe(
      "Run Meeting notes on all recordings",
    );
    expect(routineText(actions, names)).toBe(
      "Sync every watched folder, then run each namespace’s pipeline on recordings not processed yet, " +
        "then organise the graph with Organise the entity graph (propose only)",
    );
    expect(touchesGraph(actions, names)).toBe(true);
    expect(touchesGraph(actions.slice(0, 2), names)).toBe(false);
  });

  it("sends only the settings that apply", () => {
    const graph = (id: number) => names.workflow(id)?.scope === "graph";
    expect(
      cleanActions(
        [...actions, { type: "workflow", workflow: 4, recordings: "all", limit: 10, propose_only: true }],
        graph,
      ),
    ).toEqual([
      { type: "sync" },
      { type: "pipeline", recordings: "unprocessed" },
      { type: "workflow", workflow: 3, propose_only: true },
      { type: "workflow", workflow: 4, recordings: "all", limit: 10 },
    ]);
  });

  it("finds what to fix before saving", () => {
    expect(actionProblem([])).toMatch(/at least one/);
    expect(actionProblem([{ type: "sync" }, { type: "workflow", workflow: null }])).toMatch(/Action 2: choose/);
    expect(actionProblem([{ type: "sync", watches: [] }])).toMatch(/at least one folder/);
    expect(actionProblem(actions)).toBeNull();
  });

  it("sums up what each action of a run did", () => {
    expect(resultText({ type: "sync", status: "done", result: { folders: 2, new: 5, errors: 0 } })).toBe(
      "2 folders · 5 new",
    );
    expect(
      resultText({
        type: "workflow",
        status: "done",
        result: { workflow: "Organise", version: 2, applied: 1, proposed: 4, skipped: 0 },
      }),
    ).toBe("Organise v2: 1 applied · 4 proposed");
    expect(resultText({ type: "pipeline", status: "done", result: { recordings: 3, queued: 3, errors: 0 } })).toBe(
      "3 of 3 recordings queued",
    );
    expect(resultText({ type: "sync", status: "error", error: "boom" })).toBe("boom");
    expect(took("2026-10-01T03:00:00Z", "2026-10-01T03:00:42Z")).toBe("42 s");
    expect(took("2026-10-01T03:00:00Z", "2026-10-01T04:05:00Z")).toBe("1 h 5 min");
    expect(took("2026-10-01T03:00:00Z", null)).toBe("");
  });
});

describe("sensor retention actions", () => {
  it("read, save and sum up", () => {
    const all: RoutineAction = { type: "sensors" };
    const some: RoutineAction = { type: "sensors", sensors: [4, 5] };
    expect(actionText(all, names)).toBe("Tidy every sensor’s data");
    expect(actionText(some, names)).toBe("Tidy the data of 2 sensors");
    expect(cleanActions([all, some], () => false)).toEqual([{ type: "sensors" }, { type: "sensors", sensors: [4, 5] }]);
    expect(resultText({ type: "sensors", status: "done", result: { sensors: 3, readings: 120, rollups: 4 } })).toBe(
      "3 sensors · 120 readings and 4 summaries removed",
    );
    expect(
      resultText({
        type: "sensors",
        status: "done",
        result: { sensors: 1, readings: 0, rollups: 0, triaged: 2, digests: 1 },
      }),
    ).toBe("1 sensor · 0 readings and 0 summaries removed · 2 log patterns labelled · 1 daily digest");
  });
});

describe("graph workflows", () => {
  it("start valid, and use only graph nodes", () => {
    const g = starter("graph");
    expect(g.nodes.map((n) => n.type)).toEqual(["input", "candidates", "llm_judge", "filter", "apply_changes"]);
    expect(problems(g, () => undefined, "graph")).toEqual({});
    expect(inScope("candidates", "graph")).toBe(true);
    expect(inScope("save_entities", "graph")).toBe(false);
    expect(inScope("save_entities", "graph", [{ type: "save_entities", scopes: ["recording", "graph"] }])).toBe(true);
  });

  it("say what the backend would reject", () => {
    const g: WfGraph = {
      nodes: [
        { id: "in", type: "input", config: {} },
        { id: "c", type: "candidates", config: { min_confidence: 2 } },
        { id: "s", type: "save_entities", config: {} },
      ],
      edges: [
        { source: "in", target: "c" },
        { source: "c", target: "s" },
      ],
    };
    const p = problems(g, () => undefined, "graph");
    expect(p.c).toMatch(/0 to 1/);
    expect(p.s).toMatch(/not the graph/);
    expect(p[""]).toMatch(/Apply changes/);
  });

  it("sum up graph nodes on the card", () => {
    const names = { template: () => undefined, field: () => undefined };
    expect(nodeSummary({ id: "a", type: "apply_changes", config: {} }, names)).toBe("Propose every change");
    expect(nodeSummary({ id: "a", type: "apply_changes", config: { apply_above: 0.95, max_apply: 25 } }, names)).toBe(
      "Make 95%+ (up to 25), propose the rest",
    );
    expect(nodeSummary({ id: "c", type: "candidates", config: { kind: "link", min_confidence: 0.85 } }, names)).toBe(
      "across namespaces · 85%+ · up to 100",
    );
  });
});
