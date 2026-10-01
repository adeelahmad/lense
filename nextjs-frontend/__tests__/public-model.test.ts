import {
  cardLine,
  closedNote,
  collectionPath,
  descriptionRows,
  findLines,
  firstPage,
  hitWhere,
  lineAt,
  linesByPage,
  markParts,
  momentPath,
  networkNote,
  pageName,
  pagePath,
  parsePageParam,
  publicPath,
  requestLine,
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
      body: "Only members of podcasts, and people it’s shared with, can listen to it. Sign in to see it if you have access, or to ask for it.",
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

  it("says where a request for access stands", () => {
    const now = Date.parse("2026-09-30T20:00:00Z");
    expect(requestLine(null, "podcasts", false, now)).toBe(
      "Parts of this recording are closed. You can ask the owners of podcasts for access to all of it.",
    );
    expect(requestLine(undefined, "podcasts", true, now)).toBe("You can ask the owners of podcasts for access.");
    expect(requestLine({ status: "pending", at: "2026-09-30T19:00:00Z" }, "podcasts", false, now)).toBe(
      "You asked for access 1 hour ago; the owners of podcasts haven’t answered yet.",
    );
    expect(requestLine({ status: "declined", decided_at: "2026-09-28T20:00:00Z" }, "podcasts", true, now)).toBe(
      "The owners of podcasts declined your request 2 days ago. You can ask again.",
    );
  });
});

describe("networkNote", () => {
  it("tells a visitor from an IP group why they see everything", () => {
    expect(networkNote("Reading room", "recording", "public")).toBe(
      "You’re connecting from Reading room, so you see all of it, not only the parts open to everyone.",
    );
    expect(networkNote("Reading room", "recording", "private")).toBe(
      "You’re connecting from Reading room, so you see all of it.",
    );
    expect(networkNote("Campus", "collection")).toBe(
      "You’re connecting from Campus, so you see all of its recordings. Visitors elsewhere see the public ones.",
    );
  });
});

describe("a document's public page", () => {
  const segs = [
    { t0: 0, text: "a", p: 0 },
    { t0: 1000, text: "b", p: 0 },
    { t0: 2000, text: "c", p: 2 },
  ];
  it("links to its pages, and says where a search hit is", () => {
    expect(pagePath(9, 2)).toBe("/explore/recordings/9?page=3");
    expect(hitWhere(9, { t0: 4000, page: 1 })).toEqual({ label: "p. 2", href: "/explore/recordings/9?page=2" });
    expect(hitWhere(9, { t0: 65000 })).toEqual({ label: "1:05", href: "/explore/recordings/9?t=65" });
    expect([parsePageParam("3"), parsePageParam(["2", "5"]), parsePageParam("0"), parsePageParam("x")]).toEqual([
      3,
      2,
      null,
      null,
    ]);
  });
  it("names pages, groups the text by page and knows where to open", () => {
    expect([pageName([{ label: null }, { label: "ii" }], 1), pageName(null, 4)]).toEqual(["ii", "5"]);
    expect(linesByPage(segs)).toEqual([
      { page: 0, lines: [0, 1] },
      { page: 2, lines: [2] },
    ]);
    expect(firstPage(3, segs, 2, null)).toBe(1); // ?page=2
    expect(firstPage(3, segs, 9, null)).toBe(2); // within the document
    expect(firstPage(3, segs, null, 2500)).toBe(2); // the page of the line at ?t=
    expect(firstPage(3, segs, null, null)).toBe(0);
  });
  it("says what's closed in a document's or an image's words", () => {
    expect(closedNote("media", { signedIn: true, kind: "document" })).toEqual({
      title: "The pages aren’t open to everyone",
      body: "Only members of its namespace, and people it’s shared with, can see them.",
    });
    expect(closedNote("media", { signedIn: true, kind: "image" }).title).toBe("The image isn’t open to everyone");
    expect(closedNote("transcript", { signedIn: true, kind: "document" }).title).toBe(
      "The text isn’t open to everyone",
    );
    expect(closedNote("index", { signedIn: true, kind: "image" }).title).toBe("The sections aren’t open to everyone");
  });
});
