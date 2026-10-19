import { hitHref } from "@/components/recording/files-model";
import { arrivalFind, type Segment } from "@/components/recording/model";
import { recordingHref, withFind } from "@/components/search/links";
import { findText } from "@/components/search/query";

const seg = (idx: number, t0: number, text: string): Segment => ({
  idx,
  t0,
  t1: t0 + 1000,
  speaker: null,
  text,
  emotion: null,
  event: null,
});

describe("a search's words open the asset with them found", () => {
  it("keeps only the words to find", () => {
    expect(findText('budget speaker:"Host B" "next year" -draft NOT memo OR plan*')).toBe("budget next year plan");
    expect(findText("ns:pods -only")).toBe("");
  });

  it("carries them on the links from the results", () => {
    expect(withFind(recordingHref(4, 61_000), "budget -draft")).toBe("/resources/4?t=61&q=budget");
    expect(withFind(recordingHref(4), "café au lait")).toBe("/resources/4?q=caf%C3%A9%20au%20lait");
    const hit = { recording_id: 4, t0: null, file: null, line: null, page: 2 };
    expect(withFind(hitHref(hit), "speaker:Mara")).toBe("/resources/4?page=3");
  });

  it("finds the words together, else the longest word there, on the match at the linked moment", () => {
    const segs = [seg(0, 0, "The budget is late."), seg(1, 5000, "Next year's budget."), seg(2, 9000, "Budget again")];
    expect(arrivalFind(segs, "budget", 5000)).toEqual({ query: "budget", index: 1 });
    expect(arrivalFind(segs, "budget", null)).toEqual({ query: "budget", index: 0 });
    expect(arrivalFind(segs, "next year", null)).toEqual({ query: "next year", index: 0 });
    expect(arrivalFind(segs, "budget meeting", 9000)).toEqual({ query: "budget", index: 2 });
    expect(arrivalFind(segs, "hiring plan", null)).toBeNull();
  });
});
