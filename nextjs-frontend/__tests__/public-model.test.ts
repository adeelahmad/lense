import {
  cardLine,
  closedNote,
  collectionPath,
  descriptionRows,
  findLines,
  lineAt,
  markParts,
  momentPath,
  publicPath,
  safeHref,
  searchPath,
} from "@/components/public/model";

const lines = [
  { t0: 0, text: "Welcome to the café." },
  { t0: 2000, text: "We talked about the capsid results." },
  { t0: 5000, text: "The CAPSID results are in." },
];

describe("public recording page", () => {
  it("links to the pages visitors see", () => {
    expect(publicPath(12)).toBe("/explore/recordings/12");
    expect(collectionPath("podcasts")).toBe("/explore/collections/podcasts");
    expect(collectionPath("a b")).toBe("/explore/collections/a%20b");
    expect(momentPath(12, 90_500)).toBe("/explore/recordings/12?t=90");
    expect(momentPath(12, -5)).toBe("/explore/recordings/12?t=0");
    expect(searchPath("  capsid results ")).toBe("/explore/search?q=capsid%20results");
    expect(searchPath(" ")).toBe("/explore/search");
  });

  it("says when and what a card's recording is", () => {
    expect(cardLine({ recorded_at: "2025-09-12T10:00:00Z", media_kind: "video" })).toBe("12 Sept 2025 · Video");
    expect(cardLine({ recorded_at: null, media_kind: "transcript" })).toBe("Transcript only");
    expect(cardLine({ media_kind: "audio" })).toBe("Audio");
  });

  it("finds the lines with every word, ignoring case and accents", () => {
    expect(findLines(lines, "capsid results")).toEqual([1, 2]);
    expect(findLines(lines, "cafe")).toEqual([0]);
    expect(findLines(lines, "capsid welcome")).toEqual([]);
    expect(findLines(lines, "   ")).toEqual([]);
  });

  it("marks what the query found", () => {
    expect(markParts("The CAPSID results", "capsid")).toEqual([
      { text: "The ", hit: false },
      { text: "CAPSID", hit: true },
      { text: " results", hit: false },
    ]);
    expect(markParts("Welcome to the café.", "cafe")).toEqual([
      { text: "Welcome to the ", hit: false },
      { text: "café", hit: true },
      { text: ".", hit: false },
    ]);
    expect(markParts("no match here", "")).toEqual([{ text: "no match here", hit: false }]);
  });

  it("knows which line is being spoken", () => {
    expect(lineAt(lines, 0)).toBe(0);
    expect(lineAt(lines, 4999)).toBe(1);
    expect(lineAt(lines, 60_000)).toBe(2);
    expect(lineAt([{ t0: 1000 }], 500)).toBe(-1);
    expect(lineAt([], 500)).toBe(-1);
  });

  it("says who can open a closed part, and asks visitors to sign in", () => {
    expect(closedNote("media", { ns: "podcasts", signedIn: false, kind: "audio" })).toEqual({
      title: "The audio isn’t open to everyone",
      body: "Only members of podcasts, and people it’s shared with, can listen to it. Sign in if that’s you.",
    });
    expect(closedNote("media", { ns: "podcasts", signedIn: true, kind: "video" }).body).toBe(
      "Only members of podcasts, and people it’s shared with, can watch it.",
    );
    expect(closedNote("index", { signedIn: true }).title).toBe("The chapters aren’t open to everyone");
    expect(closedNote("transcript", { signedIn: true }).body).toContain("members of its namespace");
  });

  it("lays the description out as rows", () => {
    const rows = descriptionRows({
      navDate: "2026-09-12T00:00:00Z",
      language: ["en"],
      contributors: [
        { name: "Alice", role: "speaker" },
        { name: "Dana", role: "editor", uri: "https://example.org/dana" },
      ],
      subjects: [{ label: "Capsids", uri: "https://www.wikidata.org/wiki/Q193356" }, { label: "Vaccines" }],
      metadata: [{ label: { en: ["Series"] }, value: { en: ["Episode 13"] } }],
      rights: "http://creativecommons.org/licenses/by/4.0/",
      attribution: { none: ["Courtesy of the lab"] },
      provider: { name: "The Lab", homepage: "https://lab.example" },
      identifiers: [{ type: "doi", value: "10.1/x" }],
      homepage: "https://example.org/ep13",
      related: [{ id: "https://example.org/notes", label: "Show notes" }],
    });
    expect(rows.map((r) => r.label)).toEqual([
      "Date",
      "Language",
      "Speakers",
      "Contributors",
      "Subjects",
      "Series",
      "Rights",
      "Attribution",
      "Provider",
      "Identifiers",
      "Homepage",
      "Related",
    ]);
    const get = (label: string) => rows.find((r) => r.label === label)!.items;
    expect(get("Date")).toEqual([{ text: "12 September 2026" }]);
    expect(get("Language")).toEqual([{ text: "English" }]);
    expect(get("Speakers")).toEqual([{ text: "Alice", href: undefined }]);
    expect(get("Contributors")).toEqual([{ text: "Dana (editor)", href: "https://example.org/dana" }]);
    expect(get("Rights")[0].text).toBe("Attribution 4.0 (CC BY 4.0)");
    expect(get("Identifiers")).toEqual([{ text: "doi: 10.1/x" }]);
    expect(descriptionRows({})).toEqual([]);
    expect(descriptionRows(null)).toEqual([]);
  });

  it("follows only web links", () => {
    expect(safeHref("https://example.org")).toBe("https://example.org");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("/api/v1/recordings/3/audio")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });
});
