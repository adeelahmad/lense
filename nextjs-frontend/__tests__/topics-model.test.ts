import { broaderChoices, findTopics, splitLabels, topicTree, type TopicItem } from "@/components/topics/model";

const t = (id: number, label: string, broader: number[] = [], alt: string[] = []): TopicItem => ({
  id,
  label,
  alt,
  broader,
  related: [],
  recordings: 0,
  narrower: 0,
});

// Biology > Gene therapy > Viral vectors; Medicine > Gene therapy (two broader topics); Funding at the top
const items = [
  t(1, "Biology"),
  t(2, "Gene therapy", [1, 4], ["GT"]),
  t(3, "Viral vectors", [2]),
  t(4, "Medicine"),
  t(5, "funding"),
];

describe("topic tree", () => {
  it("shows top topics by label, closed", () => {
    expect(topicTree(items, new Set()).map((r) => r.topic.label)).toEqual(["Biology", "funding", "Medicine"]);
  });

  it("opens one path at a time, a topic under each of its broader topics", () => {
    const rows = topicTree(items, new Set(["1", "1/2"]));
    expect(rows.map((r) => [r.topic.label, r.depth, r.path])).toEqual([
      ["Biology", 0, "1"],
      ["Gene therapy", 1, "1/2"],
      ["Viral vectors", 2, "1/2/3"],
      ["funding", 0, "5"],
      ["Medicine", 0, "4"],
    ]);
    expect(topicTree(items, new Set(["4"])).map((r) => r.path)).toEqual(["1", "5", "4", "4/2"]);
  });

  it("cuts cycles and treats unknown broader topics as top", () => {
    const loop = [t(1, "A", [2]), t(2, "B", [1]), t(3, "C", [99])];
    expect(topicTree(loop, new Set(["1"])).map((r) => r.topic.label)).toEqual(["C"]);
  });
});

describe("topic helpers", () => {
  it("finds by label and other labels, prefix first", () => {
    expect(findTopics(items, "gt").map((x) => x.label)).toEqual(["Gene therapy"]);
    expect(findTopics(items, "me").map((x) => x.label)).toEqual(["Medicine"]);
    expect(findTopics(items, "v").map((x) => x.label)).toEqual(["Viral vectors"]);
    expect(findTopics(items, "i").map((x) => x.label)).toEqual(["Biology", "funding", "Medicine", "Viral vectors"]);
  });

  it("offers as broader only topics that aren't below it", () => {
    expect(broaderChoices(items, 1).map((x) => x.id)).toEqual([5, 4]);
    expect(broaderChoices(items, 3).map((x) => x.id)).toEqual([1, 5, 2, 4]);
  });

  it("splits other labels", () => {
    expect(splitLabels(" GT, gene  therapies ,gt,, ")).toEqual(["GT", "gene therapies"]);
  });
});
