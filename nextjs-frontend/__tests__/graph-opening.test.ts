import { openingNamespace } from "@/components/graph/model";

describe("where the graph opens when no namespace is picked", () => {
  it("stays all shared namespaces when one of them has a graph", () => {
    expect(
      openingNamespace([
        { name: "interviews", graph: "isolated", analyzed: 4, speakers: 3 },
        { name: "podcasts", graph: "shared", analyzed: 1, speakers: 0 },
      ]),
    ).toBeNull();
  });

  it("opens the namespace that keeps its graph to itself when only it has one", () => {
    expect(
      openingNamespace([
        { name: "interviews", graph: "isolated", analyzed: 2, speakers: 3 },
        { name: "podcasts", graph: "shared", analyzed: 0, speakers: 0 },
      ]),
    ).toBe("interviews");
  });

  it("picks the fullest when several keep theirs to themselves", () => {
    expect(
      openingNamespace([
        { name: "family", graph: "isolated", analyzed: 1, speakers: 1 },
        { name: "work", graph: "isolated", analyzed: 5, speakers: 2 },
        { name: "podcasts", graph: "shared" },
      ]),
    ).toBe("work");
  });

  it("changes nothing when there is nothing to see yet", () => {
    expect(
      openingNamespace([
        { name: "interviews", graph: "isolated", analyzed: 0, speakers: 0 },
        { name: "podcasts", graph: "shared", analyzed: 0, speakers: 0 },
      ]),
    ).toBeNull();
    expect(openingNamespace([])).toBeNull();
  });
});
