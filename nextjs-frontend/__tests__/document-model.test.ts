import type { Player } from "@/app/openapi-client/types.gen";
import {
  canBeTranscript,
  DEFAULT_LIMITS,
  importable,
  isUpload,
  kindOf,
  localProblem,
  mediaTypes,
  readProblem,
  uploadKindName,
} from "@/components/import/files";
import { hasSound, lengthText } from "@/components/library/model";
import {
  blocksByPage,
  clampPage,
  marksOn,
  pageAt,
  pageNumber,
  pageRef,
  pagesSummary,
  pageStart,
  parsePage,
  textNote,
  zoomStep,
} from "@/components/recording/document/model";
import { hitHref } from "@/components/recording/files-model";
import { momentLabel } from "@/components/recording/notes-model";
import { normalizePlayer, type PageInfo, type Segment } from "@/components/recording/model";

const page = (idx: number, over: Partial<PageInfo> = {}): PageInfo => ({
  idx,
  width: 1545,
  height: 2000,
  image: `/api/v1/recordings/9/frames/page-000${idx + 1}.jpg?sig=x`,
  thumb: `/api/v1/recordings/9/frames/thumb-000${idx + 1}.jpg?sig=x`,
  text: "pdf",
  chars: 120,
  label: null,
  ...over,
});
const seg = (idx: number, t0: number, page: number, box: Segment["box"] = [0.1, 0.1, 0.5, 0.05]): Segment => ({
  idx,
  t0,
  t1: t0 + 1000,
  speaker: null,
  text: `Block ${idx}`,
  emotion: null,
  event: null,
  page,
  box,
});
const SEGS = [seg(0, 0, 0), seg(1, 1000, 0), seg(2, 2000, 2, null)];

describe("a document's pages", () => {
  it("reads a document's player payload", () => {
    const m = normalizePlayer({
      id: 9,
      title: "Harbour report",
      duration_ms: null,
      segments: [
        { t0: 0, t1: 1155, s: null, text: "The harbour report", p: 0, b: [0.1, 0.05, 0.3, 0.02] },
        { t0: 1155, t1: 2000, s: null, text: "Second page", p: 1, b: null },
      ],
      media: { kind: "document", pages: 2, width: 1545, height: 2000 },
      pages: [page(0), { ...page(1), text: "ocr", label: "ii" }],
    } as unknown as Player);
    expect(m.media).toEqual({ kind: "document", pages: 2, width: 1545, height: 2000, fps: null });
    expect(m.segments.map((s) => [s.page, s.box])).toEqual([
      [0, [0.1, 0.05, 0.3, 0.02]],
      [1, null],
    ]);
    expect(m.pages[1]).toMatchObject({ idx: 1, text: "ocr", label: "ii", chars: 120 });
    expect(normalizePlayer({ media: { kind: "image" } } as unknown as Player).media.kind).toBe("image");
  });

  it("knows which page a moment of the text is on, and where each page starts", () => {
    expect(pageAt(SEGS, 0)).toBe(0);
    expect(pageAt(SEGS, 2500)).toBe(2);
    expect(pageAt([], 500)).toBe(0);
    expect([...blocksByPage(SEGS).entries()]).toEqual([
      [0, [0, 1]],
      [2, [2]],
    ]);
    expect(pageStart(SEGS, 2)).toBe(2000);
    expect(pageStart(SEGS, 1)).toBeNull(); // a page without text
  });

  it("names pages, reads ?page= and steps the zoom", () => {
    const pages = [page(0), page(1, { label: "iv" })];
    expect(pageNumber(pages, 0)).toBe("1");
    expect(pageRef(pages, 1)).toBe("p. iv");
    expect(parsePage("2", 3)).toBe(1);
    expect(parsePage("99", 3)).toBe(2);
    expect(parsePage("0", 3)).toBe(0);
    expect(parsePage("two", 3)).toBeNull();
    expect(parsePage(null, 3)).toBeNull();
    expect(clampPage(-4, 3)).toBe(0);
    expect(zoomStep(1, 1)).toBe(1.25);
    expect(zoomStep(1, -1)).toBe(0.75);
    expect(zoomStep(3, 1)).toBe(3);
    expect(zoomStep(0.5, -1)).toBe(0.5);
    expect(zoomStep(1.1, -1)).toBe(1);
  });

  it("marks the chosen block and the find matches on its page", () => {
    const hits = [
      { seg: 1, start: 0, end: 5 },
      { seg: 1, start: 6, end: 7 },
      { seg: 2, start: 0, end: 5 },
    ];
    expect(marksOn(SEGS, 0, 0, hits, 0)).toEqual([
      { box: SEGS[1].box, kind: "current-hit" },
      { box: SEGS[0].box, kind: "selected" },
    ]);
    expect(marksOn(SEGS, 0, null, hits, 2)).toEqual([{ box: SEGS[1].box, kind: "hit" }]);
    expect(marksOn(SEGS, 2, 2, hits, 2)).toEqual([]); // no box to mark
  });

  it("says how a page was read and how many there are", () => {
    expect(textNote(page(0))).toBe("Text from the PDF");
    expect(textNote(page(0, { text: "ocr" }))).toBe("Read by OCR");
    expect(textNote(page(0, { text: null }))).toBe("No text found");
    expect(pagesSummary([page(0), page(1, { text: "ocr" })], "document")).toBe("2 pages · 1 read by OCR");
    expect(pagesSummary([page(0, { text: "ocr" })], "image")).toBe("Image");
    expect(pagesSummary([page(0)], "document")).toBe("1 page");
  });

  it("puts a document's notes, rows and search matches on pages", () => {
    expect(momentLabel({ t0: 2000, t1: 3000 }, (ms) => `p. ${pageAt(SEGS, ms) + 1}`)).toBe("p. 3");
    expect(momentLabel({ t0: 2000, t1: 3000 })).toBe("0:02–0:03");
    expect(lengthText({ media_kind: "document", pages: 12, duration_ms: null })).toBe("12 pages");
    expect(lengthText({ media_kind: "image", pages: 1 })).toBe("1 page");
    expect(lengthText({ media_kind: "audio", duration_ms: 65000 })).toBe("1:05");
    expect(lengthText({ media_kind: "document", pages: null })).toBe("—");
    expect([hasSound({ media_kind: "video" }), hasSound({ media_kind: "document" })]).toEqual([true, false]);
    expect(hitHref({ recording_id: 9, t0: 2000, file: null, line: null, page: 2 })).toBe("/resources/9?page=3");
    expect(hitHref({ recording_id: 9, t0: 2000, file: null, line: null })).toBe("/resources/9?t=2");
  });
});

describe("importing documents and images", () => {
  it("knows a PDF is a document (or a transcript, if chosen) and an image an image", () => {
    expect([kindOf("Report.PDF"), kindOf("scan.tiff"), kindOf("notes.docx"), kindOf("talk.mp3")]).toEqual([
      "document",
      "image",
      "transcript",
      "audio",
    ]);
    expect([canBeTranscript("a.pdf"), canBeTranscript("a.png"), canBeTranscript("a.docx")]).toEqual([
      true,
      false,
      false,
    ]);
    expect([isUpload("document"), isUpload("image"), isUpload("transcript")]).toEqual([true, true, false]);
    expect([uploadKindName("document"), uploadKindName("image"), uploadKindName("video")]).toEqual([
      "PDF document",
      "Image",
      "Video",
    ]);
    // a source's images aren't imported from it (yet); its PDFs are, as transcripts
    expect(importable({ name: "a.png", dir: false })).toBe(false);
    expect(importable({ name: "a.pdf", dir: false })).toBe(true);
    expect(mediaTypes(DEFAULT_LIMITS)).not.toContain(".pdf");
    expect(mediaTypes(DEFAULT_LIMITS)).toContain(".mp3");
  });

  it("checks a document against the upload limits, or the transcript limits when read as one", () => {
    const pdf = { name: "big.pdf", size: 60 * 1024 * 1024 };
    expect(localProblem(pdf, DEFAULT_LIMITS)).toBeNull(); // uploads take 4 GB
    expect(localProblem(pdf, DEFAULT_LIMITS, "transcript")?.code).toBe("too-large"); // transcripts 50 MB
    const strict = { ...DEFAULT_LIMITS, extensions: [".mp3"] };
    expect(localProblem(pdf, strict)?.title).toBe("PDF files can’t be uploaded here");
    const tiny = { ...DEFAULT_LIMITS, max_mb: 1 };
    expect(localProblem({ name: "scan.png", size: 2 * 1024 * 1024 }, tiny)?.body).toBe(
      "Split it into smaller files, or ask an admin to raise the limit in Settings → Uploads.",
    );
    expect(readProblem("could not read that transcript: no transcript text found", "scan.pdf").body).toBe(
      "It may be a scan. Import it as a document instead: its pages are kept and read by OCR.",
    );
  });
});
