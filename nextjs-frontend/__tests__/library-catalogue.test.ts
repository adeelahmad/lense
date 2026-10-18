import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { catalogueRows, CATALOGUE_COLUMNS } from "@/components/library/model";
import { csvCell, toCsvText } from "@/lib/csv";

const row = (r: Partial<RecordingSummary>): RecordingSummary =>
  ({ id: 1, space: 1, media_kind: "audio", ...r }) as RecordingSummary;

describe("the library's catalogue CSV", () => {
  it("has a row per asset with its people, tags and a link to its page", () => {
    const rows = catalogueRows(
      [
        row({
          id: 7,
          title: "Call with Siobhan, part 2",
          namespace: "family",
          collection_name: "Calls",
          recorded_at: "2026-09-30T10:00:00Z",
          duration_ms: 3_725_000,
          words: 512,
          speakers: "Siobhan, Speaker 2, Aoife",
          tags: ["school", "trip"],
          language: "en",
          status: "analyzed",
          access: "private",
          origin_name: "Phone",
        }),
        row({ id: 8, title: "Scan", media_kind: "document", pages: 3 }),
      ],
      "https://lens.example/",
    );
    expect(rows[0]).toEqual([...CATALOGUE_COLUMNS]);
    expect(rows[1]).toEqual([
      "7",
      "Call with Siobhan, part 2",
      "family",
      "Calls",
      "audio",
      "2026-09-30T10:00:00Z",
      "1:02:05",
      "512",
      "Siobhan; Aoife",
      "school; trip",
      "en",
      "analyzed",
      "private",
      "Phone",
      "https://lens.example/resources/7",
    ]);
    expect(rows[2].slice(0, 7)).toEqual(["8", "Scan", "", "", "document", "", "3 pages"]);
    expect(toCsvText(rows).split("\n")[1]).toContain('"Call with Siobhan, part 2"');
  });

  it("quotes what needs it and never lets a cell run as a formula", () => {
    expect(csvCell('say "hi"')).toBe('"say ""hi"""');
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("@sum, x")).toBe('"\'@sum, x"');
    expect(csvCell("-3")).toBe("-3");
    expect(
      toCsvText([
        ["a", "b"],
        ["1", "2"],
      ]),
    ).toBe("a,b\n1,2\n");
  });
});
