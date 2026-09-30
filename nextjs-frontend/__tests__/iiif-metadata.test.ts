import { bulkValue } from "@/components/iiif/bulk-edit";
import {
  contentStateTarget,
  decodeContentState,
  formatExt,
  includedFrom,
  momentLabel,
  parseClock,
  runtime,
  schemaProblem,
} from "@/components/iiif/iiif-model";
import { importProblem } from "@/components/iiif/import";
import {
  conflictingFields,
  describeChange,
  describeEdit,
  dirtyFields,
  langGaps,
  langsOf,
  langStatus,
  patchFor,
  profileProblems,
  publishState,
  uriError,
  validate,
  withLang,
  type Meta,
} from "@/components/iiif/metadata-model";
import { canonicalRights, rightsFor, rightsShort } from "@/components/iiif/rights";

jest.mock("@/lib/api/browser", () => ({
  useApiClient: jest.fn(),
  data: jest.fn(),
  ApiError: class extends Error {},
}));

describe("language maps", () => {
  it("sets and clears one language", () => {
    expect(withLang({ none: ["A"] }, "en", "Title")).toEqual({
      none: ["A"],
      en: ["Title"],
    });
    expect(withLang({ en: ["Title"] }, "en", "")).toBeNull();
    expect(withLang(null, "pt", "Título")).toEqual({ pt: ["Título"] });
  });

  it("reports ✓ ◐ ○ per language and the gaps viewers fall back on", () => {
    const m: Meta = {
      label: { en: ["Ep 12"], pt: ["Ep 12"] },
      summary: { en: ["Two hosts…"] },
    };
    expect(langStatus(m, "en")).toBe("full");
    expect(langStatus(m, "pt")).toBe("partial");
    expect(langStatus(m, "de")).toBe("empty");
    expect(langGaps(m)).toEqual(["pt"]);
    expect(langsOf(m, ["de"], "pt")).toEqual(["pt", "en", "de"]);
  });
});

describe("validation", () => {
  it("mirrors the backend's rules", () => {
    expect(
      validate({
        label: { "en us": ["x"] },
        rights: "https://example.org/licence",
        homepage: "podcast.halden-labs.com/ep12",
        provider: { name: "", homepage: "https://x.org" },
        metadata: [{ label: { en: ["Series"] }, value: {} }],
        language: ["none"],
        creators: [{ name: "Host A", uri: "wikidata Q1" }],
        navDate: "yesterday-ish",
      }),
    ).toEqual({
      label: "“en us” isn’t a language code (use none when it’s unknown)",
      rights: "Pick a Creative Commons licence or a RightsStatements.org statement",
      homepage: "Not a valid URI — add https://",
      provider: "The provider needs a name",
      metadata: "Every pair needs a label and a value",
      language: "Use language codes such as en, de or pt-BR",
      creators: "Use an http(s) link",
      navDate: "Use a date like 2026-09-30",
    });
    expect(
      validate({
        rights: "http://creativecommons.org/licenses/by-nc/4.0/",
        homepage: "https://x.org/ep",
        label: { none: ["x"] },
      }),
    ).toEqual({});
    expect(uriError("ftp://x")).toBe("Use an http(s) address");
  });

  it("applies the namespace profile: required fields and vocabularies", () => {
    expect(
      profileProblems(
        { label: { en: ["x"] }, subjects: [{ label: "Corvid-2" }] },
        {
          required: ["label", "attribution"],
          vocabularies: { subjects: ["Safety"] },
        },
        "podcasts",
      ),
    ).toEqual([
      {
        field: "attribution",
        message: "Required attribution is required by the podcasts profile",
      },
      {
        field: "subjects",
        message: "“Corvid-2” isn’t in this namespace’s list",
      },
    ]);
  });
});

describe("changes", () => {
  it("finds changed fields and builds the save body", () => {
    const base: Meta = { label: { en: ["A"] }, subjects: [], rights: null };
    const draft: Meta = {
      label: { en: ["A "] },
      subjects: [{ label: "X" }],
      rights: "http://creativecommons.org/licenses/by/4.0/",
      homepage: "",
    };
    expect(dirtyFields(base, draft)).toEqual(["rights", "subjects"]);
    expect(patchFor({ ...draft, summary: null }, ["rights", "summary"])).toEqual({
      rights: "http://creativecommons.org/licenses/by/4.0/",
      summary: null,
    });
    expect(conflictingFields(["rights", "summary"], ["summary", "label"])).toEqual(["summary"]);
  });

  it("describes history entries in plain words", () => {
    expect(describeChange("rights", undefined, "http://creativecommons.org/licenses/by-nc/4.0/")).toBe(
      "Rights: none → CC BY-NC 4.0",
    );
    expect(describeChange("subjects", [{ label: "A" }], [{ label: "A" }, { label: "Corvid-2" }])).toBe(
      "Added subject “Corvid-2”",
    );
    expect(describeChange("summary", { en: ["x"] }, { en: ["y"], pt: ["z"] })).toBe("Summary · en, pt edited");
    expect(describeChange("homepage", "https://a", null)).toBe("Related link (homepage) cleared");
    expect(describeChange("access", "public", undefined)).toBe("Access: back to the derived value");
    expect(
      describeEdit({
        changed: ["access", "navDate"],
        before: { access: "private" },
        after: { access: "public", navDate: "2026-09-12T00:00:00Z" },
      }),
    ).toBe("Access: private → public; Date: none → 2026-09-12");
  });

  it("tells draft, private, published and needs attention apart", () => {
    expect(publishState("private", 0)).toBe("private");
    expect(publishState(undefined, 2)).toBe("draft");
    expect(publishState("transcript", 0)).toBe("published");
    expect(publishState("public", 1)).toBe("attention");
  });
});

describe("rights", () => {
  it("knows licences by URI in either scheme", () => {
    expect(rightsShort("https://creativecommons.org/licenses/by-nc/4.0")).toBe("CC BY-NC 4.0");
    expect(rightsFor("http://rightsstatements.org/vocab/InC/1.0/")?.name).toBe("In Copyright");
    expect(rightsShort(null)).toBe("none");
    expect(canonicalRights("https://creativecommons.org/publicdomain/zero/1.0/")).toBe(
      "http://creativecommons.org/publicdomain/zero/1.0/",
    );
  });
});

describe("IIIF panel", () => {
  const manifest = {
    items: [
      {
        items: [
          {
            items: [
              {
                body: {
                  id: "a",
                  type: "Sound",
                  format: "audio/mp4",
                  duration: 2838,
                  service: [{}],
                },
              },
            ],
          },
        ],
        annotations: [
          {
            items: [{ body: { id: "v", format: "text/vtt", language: "en" } }],
          },
          { id: "l1", label: { en: ["Speakers"] } },
        ],
      },
    ],
    structures: [{ id: "x/range/contents", items: [1, 2, 3] }],
    rendering: [{ format: "text/vtt" }, { format: "application/x-subrip" }],
    service: [{}],
    seeAlso: [{ format: "application/ld+json" }, { format: "application/xml" }],
  };

  it("lists what a Manifest publishes", () => {
    const rows = includedFrom(manifest);
    expect(rows.map((r) => [r.label, r.detail, r.on, Boolean(r.locked)])).toEqual([
      ["Audio", "m4a · 47:18", true, true],
      ["Transcript captions", "WebVTT · en", true, false],
      ["Speakers", "annotation layer", true, false],
      ["Chapters", "3 as structures (table of contents)", true, false],
      ["Downloads", "vtt · srt", true, false],
      ["Search inside", "Content Search 2.0", true, false],
      ["Descriptive records", "schema.org · Dublin Core", true, false],
    ]);
    expect(includedFrom({ items: [{ items: [{ items: [] }] }] })[0]).toMatchObject({ label: "Audio", on: false });
    expect(formatExt("audio/mpeg; codecs=mp3")).toBe("mp3");
  });

  it("reads times and moments", () => {
    expect(parseClock("14:29")).toBe(869);
    expect(parseClock("1:02:03")).toBe(3723);
    expect(parseClock("90")).toBe(90);
    expect(parseClock("1:75")).toBeNull();
    expect(parseClock("soon")).toBeNull();
    expect(momentLabel(869, 875)).toBe("14:29–14:35");
    expect(momentLabel(869)).toBe("14:29");
  });

  it("decodes a content state the backend made", () => {
    const token =
      "JTdCJTIyJTQwY29udGV4dCUyMiUzQSUyMmh0dHAlM0ElMkYlMkZpaWlmLmlvJTJGYXBpJTJGcHJlc2VudGF0aW9uJTJGMyUyRmNvbnRleHQuanNvbiUyMiUyQyUyMmlkJTIyJTNBJTIyaHR0cCUzQSUyRiUyRjEyNy4wLjAuMSUzQTgwMTElMkZpaWlmJTJGMSUyRnN0YXRlJTJGdCUzRDEyJTJDMTglMjIlMkMlMjJ0eXBlJTIyJTNBJTIyQW5ub3RhdGlvbiUyMiUyQyUyMm1vdGl2YXRpb24lMjIlM0ElNUIlMjJjb250ZW50U3RhdGUlMjIlNUQlMkMlMjJ0YXJnZXQlMjIlM0ElN0IlMjJpZCUyMiUzQSUyMmh0dHAlM0ElMkYlMkYxMjcuMC4wLjElM0E4MDExJTJGaWlpZiUyRjElMkZjYW52YXMlMkYxJTIzdCUzRDEyJTJDMTglMjIlMkMlMjJ0eXBlJTIyJTNBJTIyQ2FudmFzJTIyJTJDJTIycGFydE9mJTIyJTNBJTVCJTdCJTIyaWQlMjIlM0ElMjJodHRwJTNBJTJGJTJGMTI3LjAuMC4xJTNBODAxMSUyRmlpaWYlMkYxJTJGbWFuaWZlc3QlMjIlMkMlMjJ0eXBlJTIyJTNBJTIyTWFuaWZlc3QlMjIlN0QlNUQlN0QlN0Q";
    expect(contentStateTarget(decodeContentState(token))).toEqual({
      recording: 1,
      t0: 12,
      t1: 18,
    });
    expect(decodeContentState("not a state")).toBeNull();
    expect(contentStateTarget({ target: { id: "https://elsewhere.org/canvas/1" } })).toBeNull();
  });

  it("says how long a namespace runs", () => {
    expect(runtime(0)).toBe("0 min");
    expect(runtime(20_000)).toBe("under 1 min");
    expect(runtime(12 * 60_000)).toBe("12 min");
    expect(runtime(84 * 60_000)).toBe("1.4 h");
  });

  it("splits schema problems", () => {
    expect(schemaProblem("items/0: [] is too short")).toEqual({
      where: "items/0",
      what: "[] is too short",
    });
    expect(schemaProblem("bad")).toEqual({ where: "", what: "bad" });
  });
});

describe("imports and bulk edits", () => {
  it("turns the backend's refusals into the design's error cards", () => {
    expect(
      importProblem(
        "couldn't read that IIIF resource: only IIIF Presentation 3 carries audio; version 2 manifests are images only",
      ).tag,
    ).toBe("Version 2.x");
    expect(
      importProblem("couldn't read that IIIF resource: https://x isn't a IIIF JSON document (Expecting value)").tag,
    ).toBe("Not IIIF");
    expect(importProblem("couldn't read that IIIF resource: HTTP Error 401: Unauthorized").tag).toBe("Access denied");
    expect(importProblem("couldn't read that IIIF resource: use an http(s) address").tag).toBe("Address");
    expect(importProblem("couldn't read that IIIF resource: timed out").body).toContain("timed out");
  });

  it("parses bulk values", () => {
    expect(bulkValue("provider", "Halden Labs")).toEqual({
      value: { name: "Halden Labs" },
    });
    expect(bulkValue("language", "en, pt-BR")).toEqual({
      value: ["en", "pt-BR"],
    });
    expect(bulkValue("language", "english!")).toEqual({
      error: "Use language codes such as en, pt-BR",
    });
    expect(bulkValue("rights", " ")).toEqual({ error: "Choose a value" });
  });
});
