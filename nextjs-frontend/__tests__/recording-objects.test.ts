import type { ObjectTrack, PageInfo } from "@/components/recording/model";
import { boxesAt, boxesOnPage, howMuch, objectName, whereSeen } from "@/components/recording/objects-model";
import { skipReason } from "@/components/recording/jobs";

const track = (over: Partial<ObjectTrack>): ObjectTrack => ({
  label: "car",
  spans: [[3000, 6000]],
  screenMs: 3000,
  firstMs: 3000,
  count: 3,
  score: 0.8,
  frame: null,
  box: null,
  boxes: [
    [3000, 0.1, 0.1, 0.2, 0.2, 0.9],
    [4000, 0.2, 0.1, 0.2, 0.2, 0.8],
    [4000, 0.6, 0.1, 0.2, 0.2, 0.7],
    [5000, 0.3, 0.1, 0.2, 0.2, 0.8],
  ],
  paged: false,
  ...over,
});
const pages = (n: number): PageInfo[] =>
  Array.from({ length: n }, (_, idx) => ({
    idx,
    width: 100,
    height: 100,
    image: null,
    thumb: null,
    text: null,
    chars: 0,
    label: idx === 3 ? "iv" : null,
  }));

describe("objects seen", () => {
  it("are named as kinds", () => {
    expect(objectName("cell phone")).toBe("Cell phone");
    expect(objectName(" ")).toBe("Object");
  });

  it("say where they're seen, in a video or on pages", () => {
    const many = track({
      spans: [
        [0, 1000],
        [3000, 6000],
        [9000, 10000],
        [12000, 13000],
        [15000, 16000],
      ],
    });
    expect(whereSeen(track({}), [])).toBe("0:03–0:06");
    expect(whereSeen(many, [])).toBe("0:00–0:01, 0:03–0:06, 0:09–0:10 and 2 more");
    expect(
      whereSeen(
        track({
          paged: true,
          spans: [
            [0, 2],
            [3, 4],
          ],
        }),
        pages(5),
      ),
    ).toBe("p. 1–2, iv");
  });

  it("say how much of them there is", () => {
    expect(howMuch(track({}))).toBe("3 s on screen · found 3 times");
    expect(howMuch(track({ paged: true, screenMs: 1, count: 1 }))).toBe("on 1 page · found 1 time");
  });

  it("are drawn where they were found: every one on a frame or page", () => {
    const t = track({});
    expect(boxesAt(t, 4100)).toEqual([
      [0.2, 0.1, 0.2, 0.2],
      [0.6, 0.1, 0.2, 0.2],
    ]); // two cars on that frame
    expect(boxesAt(t, 5400)).toEqual([[0.3, 0.1, 0.2, 0.2]]);
    expect(boxesAt(t, 7000)).toEqual([]); // not seen then
    expect(boxesAt(track({ spans: [[0, 60000]], boxes: [[0, 0.1, 0.1, 0.1, 0.1, 0.5]] }), 50000)).toEqual([]); // too far from a frame
    expect(boxesOnPage(track({ paged: true, boxes: [[2, 0.5, 0.5, 0.1, 0.1, 0.9]] }), 2)).toEqual([
      [0.5, 0.5, 0.1, 0.1],
    ]);
    expect(boxesOnPage(track({ paged: true, boxes: [[2, 0.5, 0.5, 0.1, 0.1, 0.9]] }), 1)).toEqual([]);
  });

  it("say why the step was skipped", () => {
    expect(skipReason("objects skipped: no YOLOX model")).toBe("no YOLOX model");
    expect(skipReason("no YOLOX model")).toBe("no YOLOX model");
    expect(skipReason("  ")).toBeNull();
  });
});
