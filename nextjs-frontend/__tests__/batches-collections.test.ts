import type { RecordingSummary, Speaker } from "@/app/openapi-client/types.gen";
import { planFromParams } from "@/components/batches/plan";
import { keywords } from "@/components/chat/keywords";
import {
  datesLabel,
  fromApiScope,
  hoursShort,
  isEverything,
  recordingsLabel,
  scopeFromParams,
  scopeWords,
  toApiScope,
} from "@/components/chat/scope";
import { describeCollection } from "@/components/collections/describe";
import { lastHeard, reviewPairs } from "@/components/speakers/derive";

describe("Run on… links", () => {
  const plan = (qs: string) => planFromParams(new URLSearchParams(qs));

  it("reads each starting point", () => {
    expect(plan("recordings=1,2,x").selection).toEqual({ recordings: [1, 2] });
    expect(plan("collection=5&ns=podcasts").selection).toEqual({
      collection: 5,
    });
    expect(plan("entity=12").selection).toEqual({ entity: 12 });
    expect(plan("entity=1,3").selection).toEqual({
      filter: { entities: [1, 3] },
    });
    expect(plan("speaker=4&ns=podcasts").selection).toEqual({
      namespace: "podcasts",
      speaker: 4,
    });
    expect(plan("q=refund&ns=customer-calls").selection).toEqual({
      filter: { q: "refund", namespaces: ["customer-calls"] },
    });
    expect(plan("ns=podcasts").selection).toEqual({ namespace: "podcasts" });
    expect(plan("ns=podcasts&from=2026-01-01&media=audio").selection).toEqual({
      filter: { namespaces: ["podcasts"], from: "2026-01-01", media: "audio" },
    });
  });

  it("reads the work and a label", () => {
    expect(plan("ns=a&template=3").run).toEqual({ template: 3 });
    expect(plan("ns=a&pipeline=2").run).toEqual({ pipeline: 2 });
    expect(plan("ns=a&steps=analyze,summarize").run).toEqual({
      steps: ["analyze", "summarize"],
    });
    expect(plan("entity=1&label=Northwind%20Labs").label).toBe("Northwind Labs");
  });
});

describe("chat scope", () => {
  it("round-trips through the API shape and drops empty parts", () => {
    const s = {
      namespaces: ["podcasts"],
      recordings: [],
      speakers: [3],
      from: "",
      to: "2026-09-30",
    };
    expect(toApiScope(s)).toEqual({
      namespaces: ["podcasts"],
      speakers: [3],
      to: "2026-09-30",
    });
    expect(fromApiScope({ namespaces: ["podcasts"], speakers: ["3"], junk: 1 })).toEqual({
      namespaces: ["podcasts"],
      speakers: [3],
    });
    expect(isEverything(fromApiScope({}))).toBe(true);
  });

  it("reads links from Search and recordings", () => {
    expect(scopeFromParams(new URLSearchParams("q=red&ns=podcasts&speaker=2&recording=7"))).toEqual({
      namespaces: ["podcasts"],
      recordings: [7],
      speakers: [2],
    });
    expect(scopeFromParams(new URLSearchParams("recordings=1,2"))).toEqual({
      recordings: [1, 2],
    });
  });

  it("describes sizes and scope in words", () => {
    expect(hoursShort(702 * 3.6e6)).toBe("702 h");
    expect(hoursShort(1.2 * 3.6e6)).toBe("1.2 h");
    expect(hoursShort(125_000)).toBe("2 min");
    const byId = new Map([
      [1, { title: "Episode 12", duration_ms: 3.6e6 }],
      [2, { title: "Episode 13", duration_ms: 1.8e6 }],
    ]);
    expect(recordingsLabel([1], byId)).toEqual({ label: "Episode 12" });
    expect(recordingsLabel([1, 2], byId)).toEqual({
      label: "2 recordings",
      size: "1.5 h",
    });
    expect(scopeWords({ namespaces: ["podcasts", "customer-calls"] })).toBe("podcasts and customer-calls");
    expect(scopeWords({})).toBe("all your namespaces");
    expect(datesLabel("2026-02-12", undefined)).toMatch(/^From /);
  });

  it("picks retrieval keywords like the backend", () => {
    expect(keywords("What did the Halden Freight team say about pricing?")).toEqual([
      "halden",
      "freight",
      "team",
      "pricing",
    ]);
    expect(keywords("Is it OK?")).toEqual([]);
  });
});

describe("collections and speakers", () => {
  it("describes a collection", () => {
    expect(
      describeCollection(
        {
          kind: "filter",
          filter: { namespaces: ["research-interviews"], q: "handover" },
          shared: false,
          account: 1,
        },
        1,
      ),
    ).toBe("Filter · namespace research-interviews · “handover” · updates");
    expect(describeCollection({ kind: "fixed", filter: null, shared: true, account: 2 }, 1)).toBe(
      "Fixed list · shared with you",
    );
  });

  const sp = (id: number, display: string, suggestions: Speaker["suggestions"] = []): Speaker => ({
    id,
    label: `Speaker ${id}`,
    display,
    suggestions,
  });

  it("lists review pairs, most similar first", () => {
    const pairs = reviewPairs([
      sp(7, "Speaker 7", [{ id: 2, name: "Host B", score: 0.41 }]),
      sp(2, "Host B"),
      sp(9, "Speaker 9", [
        { id: 2, name: "Host B", score: 0.6 },
        { id: 99, name: "gone", score: 0.7 },
      ]),
    ]);
    expect(pairs.map((p) => [p.a.id, p.b.id, p.score])).toEqual([
      [9, 2, 0.6],
      [7, 2, 0.41],
    ]);
  });

  it("finds when each speaker was last heard", () => {
    const recs = [
      {
        id: 1,
        namespace: "podcasts",
        recorded_at: "2026-09-12T10:00:00",
        speakers: "Host A,Host B",
      },
      {
        id: 2,
        namespace: "podcasts",
        recorded_at: "2026-09-30T10:00:00",
        speakers: "Host B",
      },
      {
        id: 3,
        namespace: "other",
        recorded_at: "2026-10-01T10:00:00",
        speakers: "Host A",
      },
    ] as unknown as RecordingSummary[];
    const heard = lastHeard([sp(1, "Host A"), sp(2, "Host B"), sp(3, "Nobody")], recs, "podcasts");
    expect(heard.get(1)).toBe("2026-09-12T10:00:00");
    expect(heard.get(2)).toBe("2026-09-30T10:00:00");
    expect(heard.has(3)).toBe(false);
  });
});
