import { joinKind, joinPlan } from "@/components/graph/join";
import type { GraphNode } from "@/components/graph/model";
import { editTarget } from "@/components/graph/panel";

jest.mock("@/app/openapi-client", () => ({ Entities: {}, Search: {}, Speakers: {}, Topics: {} }));
jest.mock("next-auth/react", () => ({ useSession: () => ({ data: { accessToken: "t" } }) }));

const node = (id: string, kind: GraphNode["kind"], refs: number[], ns: string[]): GraphNode => ({
  id,
  kind,
  label: id,
  ns,
  weight: 1,
  refs,
  x: 0,
  y: 0,
});

const acme = node("e1", "entity", [1], ["pods"]);
const acmeInc = node("e2", "entity", [2], ["pods"]);
const acmeCalls = node("e9", "entity", [9], ["calls"]);
const joined = node("e:acme", "entity", [1, 9], ["calls", "pods"]);
const alice = node("s3", "speaker", [3], ["pods"]);
const al = node("s4", "speaker", [4], ["pods"]);
const topic = node("t5", "topic", [5], ["pods"]);

describe("editing from the graph", () => {
  const editorOf = (only: string) => (_role: "editor", ns: string) => ns === only;

  it("opens an entity or topic of one namespace you edit", () => {
    expect(editTarget(acme, editorOf("pods"))).toEqual({ kind: "entity", id: 1, ns: "pods" });
    expect(editTarget(topic, editorOf("pods"))).toEqual({ kind: "topic", id: 5, ns: "pods" });
    expect(editTarget(acmeCalls, editorOf("pods"))).toBeNull(); // a viewer there
    expect(editTarget(joined, editorOf("pods"))).toBeNull(); // several namespaces: each opens on its own
    expect(editTarget(alice, editorOf("pods"))).toBeNull(); // speakers have their own page
  });
});

describe("merging on the canvas", () => {
  it("joins entities, speakers and topics of one namespace, nothing that spans several", () => {
    expect([joinKind(acme), joinKind(alice), joinKind(topic), joinKind(joined), joinKind(null)]).toEqual([
      "entity",
      "speaker",
      "topic",
      null,
      null,
    ]);
  });

  it("merges in a namespace, links across namespaces, and says why when it can't", () => {
    expect(joinPlan(acmeInc, acme)).toBe("merge");
    expect(joinPlan(acmeCalls, acme)).toBe("link");
    expect(joinPlan(al, alice)).toBe("merge-speaker");
    expect(joinPlan(acme, acme)).toBe("Pick another node to merge into.");
    expect(joinPlan(acme, alice)).toBe("An entity merges into another entity.");
    expect(joinPlan(alice, topic)).toBe("A speaker merges into another speaker.");
    expect(joinPlan(alice, node("s8", "speaker", [8], ["calls"]))).toBe("Speakers are merged within one namespace.");
    expect(joinPlan(acme, joined)).toBe("An entity merges into another entity.");
    expect(joinPlan(node("t6", "topic", [6], ["pods"]), topic)).toBe("merge-topic");
    expect(joinPlan(topic, acme)).toBe("A topic merges into another topic.");
    expect(joinPlan(topic, node("t7", "topic", [7], ["calls"]))).toBe("Topics are merged within one namespace.");
  });
});
