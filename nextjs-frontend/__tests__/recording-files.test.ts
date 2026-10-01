import type { ResourceFile, SearchHit } from "@/app/openapi-client/types.gen";
import { groupByRecording } from "@/components/search/facets";
import {
  addedBy,
  deleteQuestion,
  detailChanges,
  extension,
  fileHref,
  fileMeta,
  fileTitle,
  guessRole,
  hitHref,
  languageProblem,
  lineTime,
  parseFileFocus,
  sizeProblem,
  typeProblem,
} from "@/components/recording/files-model";

const file = (over: Partial<ResourceFile>): ResourceFile => ({
  id: 3,
  role: "captions",
  name: "harbour.vtt",
  size: 2048,
  content_type: "text/vtt",
  language: "en",
  label: null,
  description: null,
  lines: 2,
  timed: true,
  public: false,
  created_by: "ed@x.io",
  created_by_name: null,
  download: "/api/v1/recordings/1/files/3/download?exp=1&sig=x",
  ...over,
});

describe("a resource's files", () => {
  it("guesses what a file is from its name", () => {
    expect(extension("Harbour.VTT")).toBe(".vtt");
    expect(extension("notes")).toBe("");
    expect(guessRole("harbour.srt")).toBe("captions");
    expect(guessRole("cover.JPG")).toBe("thumbnail");
    expect(guessRole("interview-ohms.xml")).toBe("index");
    expect(guessRole("chapters.json")).toBe("index");
    expect(guessRole("transcript.docx")).toBe("attachment"); // a document: it says what it is when asked
    expect(guessRole("transcript-by-hand.md")).toBe("transcript");
    expect(guessRole("release.zip")).toBe("attachment");
  });

  it("says when a file can't have a role, or is too big", () => {
    expect(typeProblem("captions", "notes.txt")).toBe("Captions files are .srt .vtt; this is .txt.");
    expect(typeProblem("thumbnail", "cover")).toBe(
      "Thumbnail files are .gif .jpeg .jpg .png .webp; this is a file without an extension.",
    );
    expect(typeProblem("index", "ohms.xml")).toBeNull();
    expect(typeProblem("attachment", "anything.bin")).toBeNull();
    expect(sizeProblem(0, 50)).toBe("The file is empty.");
    expect(sizeProblem(50 * 1024 * 1024, 50)).toBeNull();
    expect(sizeProblem(60 * 1024 * 1024, 50)).toBe("Files can be up to 50 MB; this one is 60.0 MB.");
    expect(languageProblem("")).toBeNull();
    expect(languageProblem("pt-BR")).toBeNull();
    expect(languageProblem("Portuguese!")).toBe("Use a language code such as en or pt-BR.");
  });

  it("describes a file in a row", () => {
    expect(fileTitle(file({}))).toBe("harbour.vtt");
    expect(fileTitle(file({ label: "English captions" }))).toBe("English captions");
    expect(fileMeta(file({}))).toBe("Captions · English · 2.0 KB · 2 timed lines");
    expect(fileMeta(file({ role: "translation", language: "fr", timed: false, lines: 1 }))).toBe(
      "Translation · French · 2.0 KB · 1 line, no times",
    );
    expect(fileMeta(file({ role: "attachment", language: null, lines: null, timed: null, size: 10 }))).toBe(
      "Attachment · 10 B",
    );
    expect(addedBy(file({}))).toBe("Added by ed@x.io");
    expect(addedBy(file({ created_by_name: "Ed Editor" }))).toBe("Added by Ed Editor");
    expect(addedBy(file({ created_by: null }))).toBeNull();
    expect(deleteQuestion(file({}))).toBe("Delete harbour.vtt? Its 2 lines leave search too.");
    expect(deleteQuestion(file({ lines: null, label: "Release form" }))).toBe("Delete Release form?");
    expect(lineTime({ t0: 65000, t1: 130000 })).toBe("1:05–2:10");
    expect(lineTime({ t0: 65000, t1: null })).toBe("1:05");
    expect(lineTime({ t0: null, t1: null })).toBeNull();
  });

  it("sends only the details that changed", () => {
    const f = file({ label: "Captions", description: "From the BBC." });
    expect(
      detailChanges(f, { role: "captions", label: "Captions", language: "en", description: "From the BBC." }),
    ).toEqual({});
    expect(
      detailChanges(f, { role: "transcript", label: " ", language: "en-GB", description: "From the BBC." }),
    ).toEqual({
      role: "transcript",
      label: null,
      language: "en-GB",
    });
  });

  it("links to a file's lines, and search hits to their moment or their line", () => {
    expect(fileHref(4, 3)).toBe("/resources/4?file=3");
    expect(fileHref(4, 3, 0)).toBe("/resources/4?file=3&line=0");
    const hit = { recording_id: 4, t0: 61000, file: 3, line: 2 } as SearchHit;
    expect(hitHref(hit)).toBe("/resources/4?t=61");
    expect(hitHref({ ...hit, t0: null })).toBe("/resources/4?file=3&line=2");
    expect(hitHref({ recording_id: 4, t0: 0, file: null, line: null } as SearchHit)).toBe("/resources/4");
    expect(parseFileFocus("3", "12")).toEqual({ file: 3, line: 12 });
    expect(parseFileFocus(["3", "9"], undefined)).toEqual({ file: 3, line: null });
    expect(parseFileFocus("3", "-1")).toEqual({ file: 3, line: null });
    expect(parseFileFocus("x", "1")).toBeNull();
    expect(parseFileFocus(undefined, "1")).toBeNull();
  });

  it("puts lines without times after the moments of a recording", () => {
    const h = (id: string, t0: number | null, line: number | null = null) =>
      ({
        id,
        recording_id: 1,
        title: "One",
        t0,
        t1: t0,
        line,
        snippet: id,
        source: t0 == null ? "file" : "said",
      }) as SearchHit;
    const [g] = groupByRecording([h("b", null, 4), h("c", 9000), h("a", null, 1), h("d", 2000)]);
    expect(g.hits.map((x) => x.id)).toEqual(["d", "c", "a", "b"]);
  });
});
