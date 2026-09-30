import {
  approxCount,
  approxDuration,
  confirmMatches,
  confirmNumber,
  describeSelection,
  eta,
  money,
  nothingRunnable,
  progressParts,
} from "@/components/batches/format";
import {
  filterGraph,
  findFocus,
  findNodes,
  nearestInDirection,
  neighbours,
  nodeShape,
  summarize,
  type GraphEdge,
  type GraphNode,
} from "@/components/graph/model";
import { isUnnamed, monthLabel, speakerInitials, talkByMonth, talkTime } from "@/components/speakers/format";

describe("speaker formatting", () => {
  it("reads talk time like the registry", () => {
    expect(talkTime(184_320_000)).toBe("51 h 12 m");
    expect(talkTime(7_500_000)).toBe("2 h 05 m");
    expect(talkTime(400_000)).toBe("6 m 40 s");
    expect(talkTime(3_060_000)).toBe("51 m");
    expect(talkTime(18_400)).toBe("18 s");
  });

  it("spots unnamed speakers and makes initials", () => {
    expect(isUnnamed({ name: null, label: "Speaker 7" })).toBe(true);
    expect(isUnnamed({ name: "Host B", label: "Speaker 2" })).toBe(false);
    expect(isUnnamed({ name: null, label: "Dana" })).toBe(false);
    expect(speakerInitials("Host B")).toBe("B");
    expect(speakerInitials("Ravi Menon")).toBe("RM");
    expect(speakerInitials("Speaker 7")).toBe("7");
  });

  it("sums talk time per month", () => {
    const rows = [
      { recorded_at: "2026-09-12T10:00:00", talk_ms: 1000 },
      { recorded_at: "2026-09-30T10:00:00", talk_ms: 500 },
      { recorded_at: "2026-07-01", talk_ms: 200 },
      { recorded_at: "2025-01-01", talk_ms: 999 },
    ];
    expect(talkByMonth(rows, 3, new Date("2026-09-30T12:00:00Z"))).toEqual([
      { month: "2026-07", ms: 200 },
      { month: "2026-08", ms: 0 },
      { month: "2026-09", ms: 1500 },
    ]);
    expect(monthLabel("2026-09")).toBe("Sep");
  });
});

describe("batch formatting", () => {
  it("approximates", () => {
    expect(approxDuration(40)).toBe("~40 s");
    expect(approxDuration(1680)).toBe("~28 min");
    expect(approxDuration(9000)).toBe("~2.5 h");
    expect(approxCount(270_400)).toBe("~270k");
    expect(approxCount(1_200_000)).toBe("~1.2M");
    expect(money(0.8)).toBe("$0.80");
    expect(money(null)).toBe("—");
  });

  it("checks typed confirmation", () => {
    expect(confirmMatches(" run  214 ", "RUN 214")).toBe(true);
    expect(confirmMatches("214", "RUN 214")).toBe(false);
    expect(confirmMatches("anything", null)).toBe(true);
    expect(confirmNumber("RUN 214")).toBe("214");
  });

  it("knows when nothing can run", () => {
    expect(nothingRunnable({ recordings: 18, by_kind: { transcript: 18 } }, [{ type: "transcribe" }])).toBe(true);
    expect(nothingRunnable({ recordings: 18, by_kind: { transcript: 18 } }, [{ type: "llm" }])).toBe(false);
    expect(nothingRunnable({ recordings: 0, by_kind: {} }, [{ type: "llm" }])).toBe(true);
  });

  it("describes where recordings come from", () => {
    expect(describeSelection({ entity: 4 }, { entity: "Northwind Labs" })).toBe(
      "every recording that mentions Northwind Labs",
    );
    expect(describeSelection({ namespace: "podcasts" })).toBe("every recording in podcasts");
    expect(
      describeSelection({
        filter: { q: "refund", namespaces: ["customer-calls"] },
      }),
    ).toBe("recordings matching “refund” in customer-calls");
    expect(describeSelection({ recordings: [1, 2, 3] })).toBe("3 chosen recordings");
    expect(describeSelection({ collection: 5 }, { collection: "Cohort B" })).toBe("the saved collection “Cohort B”");
  });

  it("computes progress and a time left", () => {
    const p = progressParts({
      counts: { succeeded: 23, failed: 1, running: 1 },
      done: 24,
      total: 39,
      remaining: 0,
    });
    expect(p).toEqual({
      done: 23,
      failed: 1,
      running: 1,
      queued: 14,
      total: 39,
    });
    expect(eta(p, "2026-09-30T10:00:00Z", Date.parse("2026-09-30T10:24:00Z"))).toBeCloseTo(15 * 60);
    expect(eta({ ...p, done: 0, failed: 0 }, "2026-09-30T10:00:00Z")).toBeNull();
  });
});

const N = (id: string, x: number, y: number, over: Partial<GraphNode> = {}): GraphNode => ({
  id,
  kind: "entity",
  label: id,
  type: "ORG",
  ns: ["podcasts"],
  weight: 1,
  refs: [],
  x,
  y,
  ...over,
});

describe("graph model", () => {
  const nodes = [
    N("s1", 0, 0, { kind: "speaker", label: "Host A" }),
    N("s2", 1, 0, { kind: "speaker", label: "Host B" }),
    N("e:meridian", 0, -1, { refs: [12], label: "Meridian" }),
    N("e:london", -1, 0.1, { type: "PLACE", label: "London" }),
  ];
  const edges: GraphEdge[] = [
    { a: "s1", b: "s2", w: 212, kind: "together" },
    { a: "e:meridian", b: "s1", w: 12, kind: "mentions" },
    { a: "e:london", b: "s2", w: 1, kind: "mentions" },
  ];

  it("filters by type, kind and weight, dropping unlinked entities", () => {
    const all = {
      groups: new Set(["speaker", "ORG", "PLACE"]),
      kinds: new Set(["together", "mentions"]),
      minWeight: 2,
    };
    const f = filterGraph({ nodes, edges }, all);
    expect(f.nodes.map((n) => n.id)).toEqual(["s1", "s2", "e:meridian"]);
    expect(f.edges).toHaveLength(2);
    const noSpeakers = filterGraph({ nodes, edges }, { ...all, groups: new Set(["ORG"]) });
    expect(noSpeakers.nodes).toEqual([]);
  });

  it("moves to the nearest node in a direction", () => {
    expect(nearestInDirection(nodes, "s1", "right")).toBe("s2");
    expect(nearestInDirection(nodes, "s1", "up")).toBe("e:meridian");
    expect(nearestInDirection(nodes, "s1", "left")).toBe("e:london");
    expect(nearestInDirection(nodes, "s2", "right")).toBeNull();
    expect(nearestInDirection(nodes, null, "down")).toBe("s1");
  });

  it("finds focus, neighbours, shapes and matches", () => {
    expect(findFocus(nodes, "e12")?.id).toBe("e:meridian");
    expect(findFocus(nodes, "s2")?.id).toBe("s2");
    expect(findFocus(nodes, "e99")).toBeUndefined();
    expect([...neighbours(edges, "s1")].sort()).toEqual(["e:meridian", "s2"]);
    expect(nodeShape(nodes[3])).toBe("triangle");
    expect(nodeShape(nodes[0])).toBe("circle");
    expect(findNodes(nodes, "host").map((n) => n.id)).toEqual(["s1", "s2"]);
  });

  it("summarises the picture in words", () => {
    expect(summarize(nodes, edges, "podcasts")).toBe(
      "4 nodes (2 speakers) and 3 links in podcasts. Most connected: Host A (2 links), Host B (2 links), Meridian (1 link).",
    );
    expect(summarize([], [], "podcasts")).toBe("The graph for podcasts is empty.");
  });
});
