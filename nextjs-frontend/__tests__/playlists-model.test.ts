import type { Clip } from "@/app/openapi-client/types.gen";
import {
  clipTitle,
  defaultPlaylist,
  firstPlayable,
  followCurrent,
  moveBy,
  moveTo,
  nextPlayable,
  reachedEnd,
  readdBody,
  removeItem,
  restoreItem,
  titleFromQuote,
  totalMs,
} from "@/components/playlists/model";

function clip(id: number, over: Partial<Clip> = {}): Clip {
  return {
    id,
    playlist: 1,
    position: id - 1,
    recording: 10 + id,
    t0: 1000 * id,
    t1: 1000 * id + 5000,
    media: "audio",
    audio: `/api/v1/recordings/${10 + id}/audio?sig=x`,
    link: `/resources/${10 + id}`,
    ...over,
  };
}

const ids = (xs: { id: number }[]) => xs.map((x) => x.id);

describe("playing a playlist back to back", () => {
  it("goes on to the next clip with media, skipping clips of text, and stops at the end", () => {
    const items = [clip(1), clip(2, { audio: null, media: "transcript" }), clip(3), clip(4, { audio: null })];
    expect(nextPlayable(items, 0)).toBe(2);
    expect(nextPlayable(items, 2)).toBeNull();
    expect(nextPlayable(items, -1)).toBe(0);
    expect(firstPlayable(items)).toBe(0);
    expect(firstPlayable([clip(1, { audio: null }), clip(2)])).toBe(1);
    expect(firstPlayable([clip(1, { audio: null })])).toBeNull();
  });

  it("moves on when a clip reaches its end", () => {
    const c = clip(1); // 1000 to 6000
    expect(reachedEnd(c, 5000)).toBe(false);
    expect(reachedEnd(c, 5960)).toBe(true);
    expect(reachedEnd(c, 7000)).toBe(true);
  });

  it("adds up how long the clips play", () => {
    expect(totalMs([clip(1), clip(2)])).toBe(10000);
    expect(totalMs([])).toBe(0);
  });

  it("keeps the clip playing when the list changes around it", () => {
    const before = [clip(1), clip(2), clip(3)];
    expect(followCurrent(before, moveTo(before, 0, 2), 0)).toBe(2);
    expect(followCurrent(before, removeItem(before, 2).items, 1)).toBe(1); // gone: the one now in its place
    expect(followCurrent(before, removeItem(before, 3).items, 2)).toBe(1);
    expect(followCurrent(before, [], 0)).toBeNull();
    expect(followCurrent(before, before, null)).toBeNull();
  });
});

describe("reordering", () => {
  const items = [clip(1), clip(2), clip(3), clip(4)];

  it("moves a clip by dragging it to another place", () => {
    expect(ids(moveTo(items, 0, 2))).toEqual([2, 3, 1, 4]);
    expect(ids(moveTo(items, 3, 0))).toEqual([4, 1, 2, 3]);
    expect(ids(moveTo(items, 1, 99))).toEqual([1, 3, 4, 2]);
    expect(moveTo(items, 1, 1)).toBe(items); // nothing to save
    expect(moveTo(items, 9, 0)).toBe(items);
  });

  it("moves a clip up or down one place, staying put at either end", () => {
    expect(ids(moveBy(items, 3, -1))).toEqual([1, 3, 2, 4]);
    expect(ids(moveBy(items, 3, 1))).toEqual([1, 2, 4, 3]);
    expect(moveBy(items, 1, -1)).toBe(items);
    expect(moveBy(items, 4, 1)).toBe(items);
    expect(moveBy(items, 99, 1)).toBe(items);
  });
});

describe("removing with Undo", () => {
  it("takes a clip out and puts it back where it was", () => {
    const items = [clip(1), clip(2), clip(3)];
    const { items: left, removed } = removeItem(items, 2);
    expect(ids(left)).toEqual([1, 3]);
    expect(removed).toEqual({ item: items[1], index: 1 });
    expect(ids(restoreItem(left, removed!))).toEqual([1, 2, 3]);
    // the list got shorter meanwhile: it goes at the end
    expect(ids(restoreItem([clip(1)], { item: clip(5), index: 3 }))).toEqual([1, 5]);
    expect(removeItem(items, 99)).toEqual({ items, removed: null });
  });

  it("adds it again through the API at its old place, with all it had", () => {
    const c = clip(2, { title: "Intro", note: "start here", quote: "hello", highlight: 7 });
    expect(readdBody({ item: c, index: 1 })).toEqual({
      recording: 12,
      t0: 2000,
      t1: 7000,
      title: "Intro",
      note: "start here",
      quote: "hello",
      highlight: 7,
      position: 1,
    });
    expect(readdBody({ item: clip(1, { title: null }), index: 0 }).title).toBeUndefined();
  });
});

describe("keeping a clip", () => {
  it("goes in your latest used playlist, never one someone else shared", () => {
    expect(
      defaultPlaylist([
        { id: 3, mine: false },
        { id: 5, mine: true },
        { id: 2, mine: true },
      ])?.id,
    ).toBe(5);
    expect(defaultPlaylist([{ id: 3, mine: false }])).toBeNull();
    expect(defaultPlaylist(undefined)).toBeNull();
  });

  it("is titled by the words picked, cut to a line, or by its resource and moment", () => {
    expect(titleFromQuote("  the capsid\n model  beat ")).toBe("the capsid model beat");
    expect(titleFromQuote("x".repeat(100), 10)).toBe(`${"x".repeat(9)}…`);
    expect(clipTitle(clip(1, { title: "Intro" }))).toBe("Intro");
    expect(clipTitle(clip(1, { recording_title: "Episode 1", t0: 65000 }))).toBe("Episode 1 at 1:05");
    expect(clipTitle(clip(1, { t0: 0 }))).toBe("Asset 11 at 0:00");
  });
});
