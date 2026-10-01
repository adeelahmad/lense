import type { Player } from "@/app/openapi-client/types.gen";
import { describeEdit, revertPatch } from "@/components/recording/edits";
import { joinedAt, normalizePlayer, splitPoint, textRange, wordAt, type Word } from "@/components/recording/model";

const name = (id: number | null) => (id == null ? "nobody" : `Speaker ${id}`);

describe("timed words", () => {
  it("keeps the words that fit the line", () => {
    const m = normalizePlayer({
      id: 1,
      segments: [
        {
          t0: 0,
          t1: 3000,
          s: "s1",
          text: "Hello there, Lens.",
          w: [[0, 5, 0, 400], [6, 12, 500, 900], [13, 99, 1000, 2000], [3, 2, 1, 1], "x"],
        },
        { t0: 3000, t1: 5000, s: "s2", text: "Hi." },
      ],
    } as unknown as Player);
    expect(m.segments[0].words).toEqual([
      [0, 5, 0, 400],
      [6, 12, 500, 900],
    ]);
    expect("words" in m.segments[1]).toBe(false);
  });

  it("finds the word being said", () => {
    const words: Word[] = [
      [0, 5, 0, 400],
      [6, 12, 500, 900],
      [13, 18, 1600, 2000],
    ];
    expect(wordAt(words, -1)).toBe(-1);
    expect(wordAt(words, 0)).toBe(0);
    expect(wordAt(words, 450)).toBe(0); // between words: the last one said
    expect(wordAt(words, 900)).toBe(1);
    expect(wordAt(words, 5000)).toBe(2);
    expect(wordAt([], 10)).toBe(-1);
  });

  it("makes a range over a word, across the line's pieces", () => {
    const el = document.createElement("span");
    el.innerHTML = "Hello <span>Dyno</span> Therapeutics.";
    const r = textRange(el, 6, 10);
    expect(r?.toString()).toBe("Dyno");
    expect(textRange(el, 8, 19)?.toString()).toBe("no Therapeu");
    expect(textRange(el, 30, 40)).toBeNull();
  });
});

describe("splitting and joining lines", () => {
  it("splits at the start of the word the cursor is in", () => {
    const text = "Welcome back. Today we talk about Dyno.";
    expect(splitPoint(text, text.indexOf("Today"))).toEqual({ at: 14, rest: "Today we talk about Dyno." });
    expect(splitPoint(text, text.indexOf("Today") + 3)).toEqual({ at: 14, rest: "Today we talk about Dyno." });
    expect(splitPoint(text, 13)).toEqual({ at: 13, rest: "Today we talk about Dyno." }); // on the space
    expect(splitPoint(text, 3)).toBeNull(); // inside the first word: nothing before it
    expect(splitPoint(text, text.length)).toBeNull();
    expect(splitPoint("你好世界", 2)).toEqual({ at: 2, rest: "世界" });
  });

  it("knows where the second line starts once joined", () => {
    expect(joinedAt("Welcome back. ", " Today")).toBe(14);
    expect(joinedAt("你好，", "世界")).toBe(3);
    expect(joinedAt("", "x")).toBe(0);
  });

  it("describes splits and joins in the history, without Revert", () => {
    const split = {
      kind: "split",
      before: { text: "Welcome back. Today we talk about Dyno." },
      after: { at: 14, t: 2100 },
    };
    expect(describeEdit(split, name)).toBe("Split a line before “Today we talk about…”");
    expect(describeEdit({ ...split, after: { at: 14, t: 2100, speaker: 4 } }, name)).toBe(
      "Split a line before “Today we talk about…”, the rest to Speaker 4",
    );
    expect(describeEdit({ ...split, after: { speaker: null } }, name)).toBe("Split a line, the rest to nobody");
    expect(describeEdit({ kind: "merge", before: { text: "a", next: "b" }, after: { at: 2, t: 5 } }, name)).toBe(
      "Merged two lines",
    );
    expect(revertPatch(split)).toBeNull();
    expect(revertPatch({ kind: "merge", before: {}, after: {} })).toBeNull();
    expect(revertPatch({ kind: null, before: { text: "old" }, after: { text: "new" } })).toEqual({ text: "old" });
  });
});
