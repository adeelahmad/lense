import type { FaceTrack, ScreenText, Segment, Shot } from "@/components/recording/model";
import { adjacentShot, blocks, faceBoxAt, faceLanes, fineTime, frameMs, groupByShot, initialZoom, screenTime, shotAt, textAt, timelineWindow, voiceSpans } from "@/components/recording/video/model";

const shots: Shot[] = [
  { idx: 0, t0: 0, t1: 10_000, frame: "/f/0.jpg" },
  { idx: 1, t0: 10_000, t1: 25_000, frame: "/f/1.jpg" },
  { idx: 2, t0: 25_000, t1: 40_000, frame: null },
];
const face = (over: Partial<FaceTrack> = {}): FaceTrack => ({
  id: "t1",
  local: "P1",
  face: 3,
  name: "Mara Quint",
  spans: [[0, 20_000]],
  screenMs: 20_000,
  firstMs: 0,
  boxes: [
    [0, 0.7, 0.1, 0.1, 0.2],
    [4000, 0.72, 0.1, 0.1, 0.2],
    [8000, 0.74, 0.12, 0.1, 0.2],
  ],
  score: 0.9,
  match: "face",
  cover: null,
  ...over,
});
const text = (id: string, t0: number, t1: number, t = "Q3 revenue", box: ScreenText["box"] = [0.1, 0.1, 0.3, 0.1]): ScreenText => ({ id, t0, t1, text: t, box, frame: null, edited: false });

describe("time readouts", () => {
  it("shows hundredths", () => {
    expect(fineTime(761_200)).toBe("12:41.20");
    expect(fineTime(3_725_050)).toBe("1:02:05.05");
    expect(fineTime(-5)).toBe("0:00.00");
  });
  it("steps one frame", () => {
    expect(frameMs(25)).toBe(40);
    expect(frameMs(null)).toBe(40);
    expect(Math.round(frameMs(29.97) * 100) / 100).toBe(33.37);
  });
  it("says how long someone was on screen", () => {
    expect(screenTime(45_000)).toBe("45 s");
    expect(screenTime(200_000)).toBe("3 m 20 s");
    expect(screenTime(2_460_000)).toBe("41 m");
  });
});

describe("shots", () => {
  it("finds the shot at a time and steps between shots", () => {
    expect(shotAt(shots, 12_000)).toBe(1);
    expect(adjacentShot(shots, 12_000, 1)).toBe(25_000);
    expect(adjacentShot(shots, 12_000, -1)).toBe(10_000); // well inside: restart this shot
    expect(adjacentShot(shots, 10_300, -1)).toBe(0); // just started: previous shot
    expect(adjacentShot(shots, 30_000, 1)).toBeNull();
  });
});

describe("overlays", () => {
  it("draws a face at the nearest sample while it's on screen", () => {
    expect(faceBoxAt(face(), 5000)).toEqual([0.72, 0.1, 0.1, 0.2]);
    expect(faceBoxAt(face(), 7000)).toEqual([0.74, 0.12, 0.1, 0.2]);
    expect(faceBoxAt(face(), 25_000)).toBeNull(); // off screen
    expect(faceBoxAt(face({ spans: [[0, 60_000]] }), 30_000)).toBeNull(); // no sample near enough
    expect(faceBoxAt(face({ boxes: [] }), 1000)).toBeNull();
  });
  it("draws text on screen while it's visible", () => {
    const spans = [text("a", 0, 5000), text("b", 4000, 9000), text("c", 1000, 2000, "x", null)];
    expect(textAt(spans, 4500).map((s) => s.id)).toEqual(["a", "b"]);
    expect(textAt(spans, 1500).map((s) => s.id)).toEqual(["a"]);
    expect(textAt(spans, 9000)).toEqual([]);
  });
});

describe("timeline", () => {
  it("zooms around the playhead and stays inside the video", () => {
    expect(timelineWindow(60_000, 1, 10_000)).toEqual([0, 60_000]);
    expect(timelineWindow(60_000, 4, 30_000)).toEqual([22_500, 37_500]);
    expect(timelineWindow(60_000, 4, 1000)).toEqual([0, 15_000]);
    expect(timelineWindow(60_000, 4, 59_000)).toEqual([45_000, 60_000]);
  });
  it("opens long videos on a window of about 15 minutes", () => {
    expect(initialZoom(54 * 60_000)).toBe(1);
    expect(initialZoom((3 * 60 + 42) * 60_000)).toBe(16);
  });
  it("turns spans into blocks inside the window", () => {
    expect(blocks([[0, 10_000], [50_000, 60_000], [95_000, 120_000]], [0, 100_000])).toEqual([
      [0, 10],
      [50, 10],
      [95, 5],
    ]);
    expect(blocks([[10, 20]], [0, 100_000])).toEqual([[0.01, 0.4]]); // tiny spans stay visible
    expect(blocks([[200_000, 210_000]], [0, 100_000])).toEqual([]);
  });
  it("builds voice lanes, merging short pauses", () => {
    const segs: Segment[] = [
      { idx: 0, t0: 0, t1: 1000, speaker: "s1", text: "", emotion: null, event: null },
      { idx: 1, t0: 1500, t1: 3000, speaker: "s1", text: "", emotion: null, event: null },
      { idx: 2, t0: 3000, t1: 4000, speaker: "s2", text: "", emotion: null, event: null },
      { idx: 3, t0: 9000, t1: 9500, speaker: "s1", text: "", emotion: null, event: null },
    ];
    expect(voiceSpans(segs).get("s1")).toEqual([
      [0, 3000],
      [9000, 9500],
    ]);
  });
  it("keeps the eight people with the most screen time, in order of appearance", () => {
    const many = Array.from({ length: 10 }, (_, i) => face({ id: `t${i}`, screenMs: i * 1000, firstMs: 10_000 - i }));
    const { lanes, hidden } = faceLanes(many);
    expect(hidden).toBe(2);
    expect(lanes.map((f) => f.id)).toEqual(["t9", "t8", "t7", "t6", "t5", "t4", "t3", "t2"]);
  });
});

describe("text on screen by shot", () => {
  const spans = [text("a", 1000, 2000, "Q3 revenue"), text("b", 11_000, 12_000, "Project Atlas roadmap"), text("c", 12_000, 13_000, "Owner: T. Ellery")];
  it("groups lines under the shot they appear in", () => {
    expect(groupByShot(spans, shots).map((g) => [g.index, g.lines.map((l) => l.id)])).toEqual([
      [0, ["a"]],
      [1, ["b", "c"]],
    ]);
  });
  it("filters by the search box", () => {
    expect(groupByShot(spans, shots, "atlas").map((g) => g.lines.map((l) => l.id))).toEqual([["b"]]);
    expect(groupByShot(spans, [], "").map((g) => [g.index, g.shot])).toEqual([[-1, null]]);
  });
});
