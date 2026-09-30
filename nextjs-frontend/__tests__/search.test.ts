import type { SearchHit } from "@/app/openapi-client/types.gen";
import { computeFacets, groupByRecording } from "@/components/search/facets";
import { recordingHref } from "@/components/search/links";
import {
  activeFilterCount,
  filterToken,
  fromParams,
  hasTerms,
  normalizeEmotion,
  parseQuery,
  phrases,
  prefixWords,
  toParams,
} from "@/components/search/query";
import { decodeEntities, snippetText, splitSnippet } from "@/components/search/snippet";

describe("parseQuery", () => {
  it("pulls typed filters out of the words", () => {
    expect(parseQuery('"system card" speaker:"Host B" red-teaming OR evals')).toEqual({
      text: '"system card" red-teaming OR evals',
      typed: { speaker: "Host B" },
    });
    expect(parseQuery('ns:podcasts emotion:surprise recording:"Episode 12" cyber')).toEqual({
      text: "cyber",
      typed: {
        namespace: "podcasts",
        emotion: "surprise",
        recording: "Episode 12",
      },
    });
  });

  it("leaves words with colons that aren't filters alone", () => {
    expect(parseQuery("time: 14:29 notes").text).toBe("time: 14:29 notes");
  });

  it("ignores empty filter values", () => {
    expect(parseQuery('speaker:"" cyber')).toEqual({
      text: "cyber",
      typed: {},
    });
  });
});

describe("query helpers", () => {
  it("finds prefix words and phrases", () => {
    expect(prefixWords('interp* "sparse autoencoder" red*')).toEqual(["interp", "red"]);
    expect(phrases('interp* "sparse autoencoder"')).toEqual(["sparse autoencoder"]);
  });

  it("knows when there is something to search for", () => {
    expect(hasTerms("OR")).toBe(false);
    expect(hasTerms(" ? ")).toBe(false);
    expect(hasTerms("cyber OR bio")).toBe(true);
  });

  it("formats tokens and emotions", () => {
    expect(filterToken("speaker", "Host B")).toBe('speaker:"Host B"');
    expect(filterToken("namespace", "podcasts")).toBe("namespace:podcasts");
    expect(normalizeEmotion("SURPRISE")).toBe("Surprise");
  });

  it("round-trips URL state", () => {
    const qs = toParams("red teaming", {
      namespace: "podcasts",
      speaker: 4,
      emotion: "Surprise",
    });
    expect(qs).toBe("q=red+teaming&ns=podcasts&speaker=4&emotion=Surprise");
    expect(fromParams(new URLSearchParams(qs))).toEqual({
      q: "red teaming",
      filters: {
        namespace: "podcasts",
        speaker: 4,
        emotion: "Surprise",
        recording: undefined,
      },
    });
    expect(fromParams(new URLSearchParams("speaker=abc")).filters.speaker).toBeUndefined();
    expect(activeFilterCount({ namespace: "podcasts", recording: 3 })).toBe(2);
  });
});

describe("snippets", () => {
  it("splits marks and decodes entities without parsing HTML", () => {
    expect(splitSnippet("Hi, it&#x27;s <mark>Alice</mark> &lt;script&gt; &amp; co")).toEqual([
      { text: "Hi, it's ", mark: false },
      { text: "Alice", mark: true },
      { text: " <script> & co", mark: false },
    ]);
    expect(snippetText("a <mark>b</mark> c")).toBe("a b c");
    expect(decodeEntities("&quot;x&quot; &#39;y&#39; &unknown;")).toBe("\"x\" 'y' &unknown;");
  });
});

const hit = (over: Partial<SearchHit>): SearchHit => ({
  id: 1,
  recording_id: 1,
  t0: 0,
  t1: 1000,
  snippet: "",
  source: "said",
  ...over,
});

describe("facets and groups", () => {
  const hits = [
    hit({
      id: 1,
      recording_id: 1,
      title: "Episode 12",
      namespace: "podcasts",
      speaker_id: 2,
      speaker: "Alice",
      emotion: "Neutral",
      t0: 9000,
    }),
    hit({
      id: 2,
      recording_id: 1,
      title: "Episode 12",
      namespace: "podcasts",
      speaker_id: 1,
      speaker: "Bob",
      emotion: "Surprise",
      t0: 2000,
    }),
    hit({
      id: 3,
      recording_id: 3,
      title: "Renewal call",
      namespace: "customer-calls",
      speaker_id: 5,
      speaker: "Alice",
      emotion: "Unknown",
    }),
  ];

  it("counts values and tells same-named speakers apart", () => {
    const f = computeFacets(hits);
    expect(f.namespaces.map((x) => [x.key, x.count])).toEqual([
      ["podcasts", 2],
      ["customer-calls", 1],
    ]);
    expect(f.speakers.find((s) => s.key === "2")?.sub).toBe("podcasts");
    expect(f.speakers.find((s) => s.key === "1")?.sub).toBeUndefined();
    expect(f.emotions.map((e) => e.key).sort()).toEqual(["Neutral", "Surprise"]);
    expect(f.recordings[0]).toMatchObject({ key: "1", count: 2, id: 1 });
  });

  it("groups by recording in rank order with moments in time order", () => {
    const g = groupByRecording(hits);
    expect(g.map((x) => x.recordingId)).toEqual([1, 3]);
    expect(g[0].hits.map((h) => h.t0)).toEqual([2000, 9000]);
  });

  it("links to the moment in whole seconds", () => {
    expect(recordingHref(12, 869_400)).toBe("/recordings/12?t=869");
    expect(recordingHref(12, 0)).toBe("/recordings/12");
  });
});
