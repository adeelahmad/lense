import type { Comment, Highlight } from "@/app/openapi-client/types.gen";
import {
  COLOURS,
  colourOf,
  commentsAt,
  deleteCommentQuestion,
  highlightRanges,
  highlightRuns,
  highlightTitle,
  newCommentBody,
  newHighlightBody,
  openThreads,
  resolvedBy,
  threads,
} from "@/components/recording/comments-model";
import type { Segment } from "@/components/recording/model";

const comment = (over: Partial<Comment>): Comment => ({
  id: 1,
  recording: 5,
  parent: null,
  text: "Is this sourced?",
  t0: null,
  t1: null,
  quote: null,
  resolved: false,
  created_by: "vi@x.io",
  created_by_name: null,
  mine: true,
  can_resolve: true,
  can_delete: true,
  ...over,
});
const highlight = (over: Partial<Highlight>): Highlight => ({
  id: 1,
  recording: 5,
  t0: 4000,
  t1: 9000,
  quote: "the capsid model beat",
  colour: "yellow",
  label: null,
  created_by: "ed@x.io",
  created_by_name: "Ana",
  mine: false,
  can_edit: false,
  ...over,
});
const seg = (over: Partial<Segment>): Segment => ({
  idx: 0,
  t0: 0,
  t1: 10000,
  speaker: "s1",
  text: "The capsid model beat the human designs",
  emotion: null,
  event: null,
  ...over,
});

describe("comments", () => {
  it("threads them and counts the open ones", () => {
    const whole = comment({ id: 1 });
    const at = comment({ id: 2, t0: 4000, t1: 9000, resolved: true });
    const r1 = comment({ id: 3, parent: 2, mine: false });
    const r2 = comment({ id: 4, parent: 1 });
    const orphan = comment({ id: 5, parent: 99 }); // its thread is gone: not shown
    expect(threads([whole, at, r1, r2, orphan])).toEqual([
      { root: whole, replies: [r2] },
      { root: at, replies: [r1] },
    ]);
    expect(openThreads([whole, at, r1, r2])).toBe(1);
    // a line from 5 s to 6 s has the thread at 4–9 s; replies never mark a line
    expect(commentsAt([whole, at, r1, r2], 5000, 6000)).toEqual([at]);
    expect(commentsAt([whole, at, r1, r2], 0, 1000)).toEqual([]);
  });

  it("says who resolved a thread and asks before deleting", () => {
    expect(resolvedBy(comment({}))).toBeNull();
    expect(resolvedBy(comment({ resolved: true, resolved_by: "ed@x.io", resolved_by_name: "Ana" }))).toBe(
      "Resolved by Ana",
    );
    expect(resolvedBy(comment({ resolved: true, resolved_by: "ed@x.io" }))).toBe("Resolved by ed@x.io");
    expect(resolvedBy(comment({ resolved: true }))).toBe("Resolved by someone");
    expect(deleteCommentQuestion(comment({}), 0)).toBe("Delete your comment for everyone?");
    expect(deleteCommentQuestion(comment({}), 1)).toBe("Delete your comment and its reply for everyone?");
    expect(deleteCommentQuestion(comment({ mine: false, created_by_name: "Ana" }), 3)).toBe(
      "Delete Ana’s comment and its 3 replies for everyone?",
    );
    expect(deleteCommentQuestion(comment({ parent: 1, mine: false, created_by: "ed@x.io" }), 0)).toBe(
      "Delete ed@x.io’s reply for everyone?",
    );
  });

  it("builds the body for a new comment or reply", () => {
    const draft = { t0: 4000, t1: 9000, quote: "the capsid" };
    expect(newCommentBody("  Sourced? ", { draft, at: 12000 })).toEqual({
      text: "Sourced?",
      t0: 4000,
      t1: 9000,
      quote: "the capsid",
    });
    expect(newCommentBody("Here", { draft: null, at: 12345.6 })).toEqual({ text: "Here", t0: 12346 });
    expect(newCommentBody("All of it", { draft: null, at: null })).toEqual({ text: "All of it" });
    // a reply is about its thread: the draft and the clock are ignored
    expect(newCommentBody("Yes", { draft, at: 12000, parent: 7 })).toEqual({ text: "Yes", parent: 7 });
    expect(newCommentBody("No words", { draft: { ...draft, quote: "" }, at: null })).toEqual({
      text: "No words",
      t0: 4000,
      t1: 9000,
    });
  });
});

describe("highlights", () => {
  it("come in the token colours, and are named by their label or words", () => {
    expect(COLOURS.map((c) => c.value)).toEqual(["yellow", "green", "blue", "red"]);
    expect(colourOf("green").label).toBe("Green");
    expect(colourOf("pink")).toBe(COLOURS[0]);
    expect(highlightTitle(highlight({ label: "Key claim" }))).toBe("Key claim");
    expect(highlightTitle(highlight({}))).toBe("the capsid model beat");
    expect(highlightTitle(highlight({ quote: "w".repeat(100) }))).toBe(`${"w".repeat(79)}…`);
    expect(highlightTitle(highlight({ quote: null, colour: "red" }))).toBe("Red highlight");
    expect(newHighlightBody({ t0: 4000, t1: 9000, quote: "the capsid" }, "blue")).toEqual({
      t0: 4000,
      t1: 9000,
      colour: "blue",
      quote: "the capsid",
    });
    expect(newHighlightBody({ t0: 4000, t1: 4000, quote: "" })).toEqual({ t0: 4000, t1: 4000, colour: "yellow" });
  });

  it("finds where a highlight falls in a line's text", () => {
    const h = highlight({ id: 3, colour: "green", label: "Claim" });
    // a line the passage covers whole
    expect(highlightRanges(seg({ t0: 5000, t1: 8000 }), [h])).toEqual([
      { start: 0, end: 39, id: 3, colour: "green", label: "Claim" },
    ]);
    // one it doesn't touch
    expect(highlightRanges(seg({ t0: 9001, t1: 12000 }), [h])).toEqual([]);
    expect(highlightRanges(seg({ text: "" }), [h])).toEqual([]);
    // timed words: from the first word that ends after it starts to the last that starts before it ends
    const timed = seg({
      t0: 0,
      t1: 10000,
      words: [
        [0, 3, 0, 1000],
        [4, 10, 1000, 3000],
        [11, 16, 3000, 5000],
        [17, 21, 5000, 7000],
        [22, 25, 7000, 8000],
        [26, 31, 8000, 9500],
        [32, 39, 9500, 10000],
      ],
    });
    expect(highlightRanges(timed, [h])).toEqual([{ start: 11, end: 31, id: 3, colour: "green", label: "Claim" }]);
    // untimed words: in proportion to the line's time, as a selection's time is read from its place in the text
    expect(highlightRanges(seg({}), [h])).toEqual([{ start: 16, end: 35, id: 3, colour: "green", label: "Claim" }]);
    expect(highlightRanges(seg({}), [h, highlight({ id: 4, t0: 0, t1: 1000 })])).toEqual([
      { start: 16, end: 35, id: 3, colour: "green", label: "Claim" },
      { start: 0, end: 4, id: 4, colour: "yellow", label: null },
    ]);
  });

  it("lays highlights under find hits and entity names", () => {
    const text = "The capsid model beat the human designs";
    const hl = { start: 4, end: 21, id: 3, colour: "green" as const, label: null };
    const runs = highlightRuns(text, [{ start: 11, end: 16, kind: "hit" }], [hl]);
    expect(runs).toEqual([
      { text: "The ", kind: null, hl: null },
      { text: "capsid ", kind: null, hl },
      { text: "model", kind: "hit", hl },
      { text: " beat", kind: null, hl },
      { text: " the human designs", kind: null, hl: null },
    ]);
    // a hit that crosses the highlight's edge is split there, keeping its mark
    expect(
      highlightRuns(text, [{ start: 17, end: 25, kind: "hit" }], [hl]).map((r) => [r.text, r.kind, r.hl?.id]),
    ).toEqual([
      ["The ", null, undefined],
      ["capsid model ", null, 3],
      ["beat", "hit", 3],
      [" the", "hit", undefined],
      [" human designs", null, undefined],
    ]);
    // the first of overlapping marks wins; nothing marked is one run; ranges past the text are clamped
    expect(highlightRuns("abc", [], [])).toEqual([{ text: "abc", kind: null, hl: null }]);
    expect(
      highlightRuns(
        "abcdef",
        [
          { start: 0, end: 4, kind: "entity" },
          { start: 2, end: 9, kind: "hit" },
        ],
        [],
      ),
    ).toEqual([
      { text: "abcd", kind: "entity", hl: null },
      { text: "ef", kind: "hit", hl: null },
    ]);
  });
});
