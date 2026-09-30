import {
  formatName,
  initialMapping,
  isUntimed,
  kindOf,
  localProblem,
  mappedNames,
  mappingParam,
  namespaceNameProblem,
  parseMapping,
  readProblem,
  titleFromName,
} from "@/components/import/files";

describe("what a dropped file is", () => {
  it("sorts files by extension", () => {
    expect(kindOf("ep14-transcript.SRT")).toBe("transcript");
    expect(kindOf("notes.docx")).toBe("transcript");
    expect(kindOf("ep14.m4a")).toBe("audio");
    expect(kindOf("clip.mov")).toBe("video");
    expect(kindOf("townhall.pages")).toBe("unsupported");
    expect(kindOf("README")).toBe("unsupported");
  });

  it("says why a file can't be imported before sending it", () => {
    expect(localProblem({ name: "ep14.m4a", size: 1 })?.code).toBe("audio");
    expect(localProblem({ name: "townhall.pages", size: 1 })).toMatchObject({ code: "unsupported", title: "PAGES files can’t be imported" });
    expect(localProblem({ name: "big.srt", size: 60 * 1024 * 1024 }, 50)).toMatchObject({ code: "too-large", title: "Too large to upload here (limit 50 MB)" });
    expect(localProblem({ name: "ok.srt", size: 1024 })).toBeNull();
  });

  it("turns the server's parse errors into problem cards", () => {
    expect(readProblem("could not read that transcript: no transcript text found", "scan.pdf").title).toBe("This PDF has no text to import");
    expect(readProblem("no transcript text found", "notes.txt").title).toBe("No transcript text found");
    expect(readProblem("files up to 50 MB", "x.txt").code).toBe("too-large");
    expect(readProblem("could not read that transcript: bad JSON", "x.json")).toMatchObject({ code: "unreadable", body: "Bad JSON" });
  });

  it("names formats and knows which have no timings", () => {
    expect(formatName("srt")).toBe("SubRip (.srt)");
    expect(formatName("text")).toBe("Plain text");
    expect(formatName("weird")).toBe("WEIRD");
    expect(isUntimed("markdown")).toBe(true);
    expect(isUntimed("vtt")).toBe(false);
    expect(titleFromName("interview_09 p09.docx")).toBe("interview 09 p09");
  });

  it("checks namespace names like the backend", () => {
    expect(namespaceNameProblem("board-meetings")).toBeNull();
    expect(namespaceNameProblem("")).toBe("Choose a namespace.");
    expect(namespaceNameProblem("Board Meetings")).toMatch(/lowercase/);
    expect(namespaceNameProblem("-x")).toMatch(/lowercase/);
  });
});

describe("speaker mapping text", () => {
  const labels = ["SPEAKER_00", "SPEAKER_01"];

  it("starts with each label mapped to itself", () => {
    expect(initialMapping(labels)).toBe("SPEAKER_00 = SPEAKER_00\nSPEAKER_01 = SPEAKER_01");
  });

  it("parses one mapping per line and sends only real changes, without spaces around =", () => {
    const m = parseMapping("SPEAKER_00 = Host A\n\n# a comment\nSPEAKER_01 = SPEAKER_01", labels);
    expect(m.errors).toEqual([]);
    expect(m.pairs).toHaveLength(2);
    expect(mappingParam(m)).toBe("SPEAKER_00=Host A");
    expect(mappingParam(parseMapping(initialMapping(labels), labels))).toBeNull();
    expect(mappedNames(labels, m).get("SPEAKER_01")).toBe("SPEAKER_01");
  });

  it("an empty right-hand side keeps the label", () => {
    const m = parseMapping("SPEAKER_00 =", labels);
    expect(m.pairs[0]).toMatchObject({ label: "SPEAKER_00", name: "SPEAKER_00" });
    expect(mappingParam(m)).toBeNull();
  });

  it("rejects what the API can't carry and warns about unknown labels", () => {
    expect(parseMapping("SPEAKER_00 Host A", labels).errors[0]).toMatch(/label = speaker/);
    expect(parseMapping("= Host A", labels).errors[0]).toMatch(/label before/);
    expect(parseMapping("SPEAKER_00 = Smith, Jane", labels).errors[0]).toMatch(/can’t contain/);
    expect(parseMapping("SPEAKER_00 = A\nSPEAKER_00 = B", labels).errors[0]).toMatch(/mapped twice/);
    expect(parseMapping("S9 = A", labels).warnings[0]).toMatch(/isn’t a label/);
  });
});
