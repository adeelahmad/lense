import type { Player } from "@/app/openapi-client/types.gen";
import { describeEdit, revertPatch, wordDiff } from "@/components/recording/edits";
import {
  adjacentTurnStart,
  ariaTimeText,
  axisTicks,
  chapterAt,
  cleanTitle,
  entityRanges,
  findInSegments,
  fold,
  groupTurns,
  laneBars,
  normalizeEnvelope,
  normalizePlayer,
  parseStart,
  segmentAt,
  showEmotion,
  speakerSpans,
  speakerStats,
  splitRuns,
  transcribedFraction,
  turnOf,
  type Segment,
} from "@/components/recording/model";
import { humanKey, parseTime, summaryDoc } from "@/components/recording/summary-model";
import { fitTabs } from "@/components/recording/tab-fit";
import { applyChatEvent, citeParts, isRecordingChat, newAnswer } from "@/components/recording/chat-model";
import { silenceGaps, skipTarget } from "@/components/player/media";

const seg = (idx: number, t0: number, t1: number, speaker: string | null, text = "x"): Segment => ({
  idx,
  t0,
  t1,
  speaker,
  text,
  emotion: null,
  event: null,
});

// The seeded backend's player payload for "Episode 12" (trimmed).
const PLAYER = {
  id: 1,
  title: "Episode 12 — Reading a model system card",
  namespace: "podcasts",
  recorded_at: "2026-09-30T13:05:59",
  duration_ms: 30510,
  audio: null,
  speakers: [
    { key: "s2", id: 2, name: "Alice", color: "#5B7F2B" },
    { key: "s1", id: 1, name: "Bob", color: "#C2571A" },
  ],
  segments: [
    {
      t0: 0,
      t1: 6545,
      s: "s2",
      text: "Welcome back. Today we talk about Dyno Therapeutics.",
      e: "Neutral",
      v: null,
    },
    {
      t0: 6795,
      t1: 11415,
      s: "s1",
      text: "Wow, really?",
      e: "Surprise",
      v: null,
    },
    {
      t0: 11665,
      t1: 17440,
      s: "s2",
      text: "Yes.",
      e: "Unknown",
      v: "Laughter",
    },
  ],
  sections: [
    {
      idx: 0,
      seg0: 0,
      seg1: 3,
      t0: 0,
      t1: 30510,
      title: "Dyno Therapeutics, capsid, design",
    },
  ],
  entities: [{ name: "Dyno Therapeutics", type: "ORG", segs: [0, 1] }],
  keywords: [
    ["dyno therapeutics", 3.8],
    ["capsid", 3.3],
  ],
  envelope: null,
  summary: null,
  media: { kind: "audio", width: null, height: null, fps: null },
  labels: {},
} as unknown as Player;

describe("normalizePlayer", () => {
  it("parses the seeded transcript-only payload", () => {
    const m = normalizePlayer(PLAYER);
    expect(m.audio).toBeNull();
    expect(m.media.kind).toBe("audio");
    expect(m.speakers.map((s) => [s.key, s.name, s.index, s.color])).toEqual([
      ["s2", "Alice", 0, "var(--spk-1)"],
      ["s1", "Bob", 1, "var(--spk-2)"],
    ]);
    expect(m.segments[1]).toEqual({
      idx: 1,
      t0: 6795,
      t1: 11415,
      speaker: "s1",
      text: "Wow, really?",
      emotion: "Surprise",
      event: null,
    });
    expect(m.chapters[0].title).toBe("Dyno Therapeutics, capsid, design");
    expect(m.keywords).toEqual([
      { text: "dyno therapeutics", weight: 3.8 },
      { text: "capsid", weight: 3.3 },
    ]);
    expect(m.envelope).toBeNull();
    expect(m.shots).toEqual([]);
    expect(m.facesMode).toBe("off");
  });

  it("parses a video payload: shots, text on screen with boxes, face tracks", () => {
    const m = normalizePlayer({
      ...PLAYER,
      media: { kind: "video", width: 1920, height: 1080, fps: 30 },
      shots: [{ idx: 0, t0: 0, t1: 5000, frame: "/f/0.jpg" }],
      screen_text: [
        {
          id: "ocr:1",
          t0: 100,
          t1: 900,
          text: "Q3",
          box: [0.1, 0.1, 0.2, 0.05],
          frame: "/f/1.jpg",
          edited: true,
        },
      ],
      faces_mode: "recognize",
      faces: [
        {
          id: "ft1",
          local: "P1",
          face: 3,
          name: "Mara",
          spans: [[0, 4000]],
          screen_ms: 4000,
          first_ms: 0,
          boxes: [[0, 0.5, 0.1, 0.1, 0.2]],
          score: 0.9,
          match: "face",
          cover: "/c.jpg",
        },
      ],
    } as unknown as Player);
    expect(m.media).toEqual({
      kind: "video",
      width: 1920,
      height: 1080,
      fps: 30,
    });
    expect(m.screenText[0]).toMatchObject({
      id: "ocr:1",
      box: [0.1, 0.1, 0.2, 0.05],
      edited: true,
    });
    expect(m.faces[0]).toMatchObject({
      id: "ft1",
      face: 3,
      name: "Mara",
      spans: [[0, 4000]],
      boxes: [[0, 0.5, 0.1, 0.1, 0.2]],
      cover: "/c.jpg",
    });
    expect(m.facesMode).toBe("recognize");
  });

  it("never throws on missing or odd fields", () => {
    const m = normalizePlayer({ id: 5 } as Player);
    expect(m.title).toBe("Untitled recording");
    expect(m.segments).toEqual([]);
    expect(m.durationMs).toBe(0);
  });

  it("stretches the duration to the last line when the stored one is short", () => {
    const m = normalizePlayer({ ...PLAYER, duration_ms: 1000 } as Player);
    expect(m.durationMs).toBe(17440);
  });
});

describe("cleanTitle", () => {
  it("collapses whitespace the way the API stores titles", () => {
    expect(cleanTitle("  Capsid   design\nepisode ")).toBe("Capsid design episode");
    expect(cleanTitle(" \t ")).toBe("");
  });
});

describe("normalizeEnvelope", () => {
  it("scales bytes to 0..1 and keeps fractions", () => {
    expect(normalizeEnvelope([0, 255, 51])).toEqual([0, 1, 0.2]);
    expect(normalizeEnvelope([0, 0.5, 1])).toEqual([0, 0.5, 1]);
  });
  it("is null for silence, empty or junk", () => {
    expect(normalizeEnvelope([])).toBeNull();
    expect(normalizeEnvelope([0, 0])).toBeNull();
    expect(normalizeEnvelope(null)).toBeNull();
    expect(normalizeEnvelope({})).toBeNull();
  });
});

describe("turns", () => {
  const segs = [
    seg(0, 0, 1000, "a"),
    seg(1, 1000, 2000, "a"),
    seg(2, 2000, 3000, "b"),
    seg(3, 3000, 4000, null),
    seg(4, 4000, 5000, null),
    seg(5, 5000, 6000, "b"),
  ];
  it("groups consecutive lines by speaker; unassigned lines stay one per turn", () => {
    expect(groupTurns(segs).map((t) => t.segs)).toEqual([[0, 1], [2], [3], [4], [5]]);
  });
  it("cuts long monologues", () => {
    const long = Array.from({ length: 9 }, (_, i) => seg(i, i * 1000, i * 1000 + 900, "a"));
    expect(groupTurns(long, 4).map((t) => t.segs.length)).toEqual([4, 4, 1]);
    expect(groupTurns(long, 100, 3000).map((t) => t.segs.length)).toEqual([3, 3, 3]);
  });
  it("finds the playing line, keeping the last one lit through a pause", () => {
    expect(segmentAt(segs, -5)).toBe(-1);
    expect(segmentAt(segs, 0)).toBe(0);
    expect(segmentAt(segs, 2500)).toBe(2);
    expect(segmentAt(segs, 99999)).toBe(5);
  });
  it("maps segments to turns and steps between turns (↑/↓)", () => {
    const turns = groupTurns(segs);
    expect(turnOf(turns, 1)).toBe(0);
    expect(turnOf(turns, 4)).toBe(3);
    expect(adjacentTurnStart(turns, 500, 1)).toBe(2000);
    expect(adjacentTurnStart(turns, 2200, -1)).toBe(0); // 200 ms into a turn: back to the previous one
    expect(adjacentTurnStart(turns, 4800, -1)).toBe(3000);
    expect(adjacentTurnStart(turns, 1900, -1)).toBe(0); // well into the first turn: its start
    expect(adjacentTurnStart(turns, 5500, 1)).toBeNull();
  });
  it("finds the chapter at a time", () => {
    const ch = [
      { idx: 0, seg0: 0, seg1: 2, t0: 0, t1: 2000, title: "A" },
      { idx: 1, seg0: 2, seg1: 6, t0: 2000, t1: 6000, title: "B" },
    ];
    expect(chapterAt(ch, 1999)).toBe(0);
    expect(chapterAt(ch, 2000)).toBe(1);
  });
});

describe("lanes", () => {
  const segs = [seg(0, 0, 4000, "a"), seg(1, 4050, 6000, "a"), seg(2, 6000, 10000, "b")];
  it("builds each speaker's timeline from timestamps, merging near-touching spans", () => {
    const m = speakerSpans(segs, 10000, 0.01);
    expect(m.get("a")).toEqual([[0, 0.6]]);
    expect(m.get("b")).toEqual([[0.6, 1]]);
  });
  it("credits each envelope bar to whoever is talking", () => {
    const lanes = laneBars([0.2, 0.9, 0.4, 0.8], segs, 10000, 4, ["a", "b"]);
    expect(lanes.get("a")).toEqual([0.2, 0.9, 0, 0]);
    expect(lanes.get("b")).toEqual([0, 0, 0.4, 0.8]);
  });
  it("draws mid-height bars when there's no envelope", () => {
    expect(laneBars(null, segs, 10000, 2, ["a", "b"]).get("a")).toEqual([0.5, 0]);
  });
  it("measures how far transcription has got", () => {
    expect(transcribedFraction(segs, 20000)).toBe(0.5);
    expect(transcribedFraction([], 20000)).toBe(0);
  });
});

describe("labels", () => {
  it("writes the slider's aria-valuetext", () => {
    expect(ariaTimeText(872_000, 2_838_000, "Host B", 4)).toBe("14:32 of 47:18, Host B, chapter 5");
    expect(ariaTimeText(0, 30_000)).toBe("0:00 of 0:30");
  });
  it("picks round axis ticks and ends on the duration", () => {
    expect(axisTicks(30_510)).toEqual([0, 10_000, 20_000, 30_510]);
    expect(axisTicks(2_838_000)).toEqual([0, 600_000, 1_200_000, 1_800_000, 2_400_000, 2_838_000]);
  });
  it("reads ?t= in seconds or as a clock time", () => {
    expect(parseStart("872")).toBe(872);
    expect(parseStart("872.5")).toBe(872.5);
    expect(parseStart("14:32")).toBe(872);
    expect(parseStart("1:02:03")).toBe(3723);
    expect(parseStart(["30", "40"])).toBe(30);
    expect(parseStart("soon")).toBeNull();
    expect(parseStart(undefined)).toBeNull();
  });
  it("hides Unknown emotions", () => {
    expect(showEmotion("Neutral")).toBe(true);
    expect(showEmotion("Unknown")).toBe(false);
    expect(showEmotion(null)).toBe(false);
  });
  it("computes talk-time shares from the recording's stats", () => {
    const s = speakerStats({
      speakers: [
        {
          speaker_id: 2,
          talk_ms: 3000,
          turns: 3,
          words: 40,
          wpm: 150,
          name: "Alice",
        },
        { talk_ms: 1000, name: "Unattributed" },
      ],
    });
    expect(s.map((x) => [x.id, x.share])).toEqual([
      [2, 0.75],
      [null, 0.25],
    ]);
  });
});

describe("find and highlights", () => {
  const segs = [seg(0, 0, 1, "a", "Cyber evals and CYBER teams"), seg(1, 1, 2, "a", "Café culture")];
  it("finds case- and accent-insensitive matches with offsets", () => {
    expect(findInSegments(segs, "cyber")).toEqual([
      { seg: 0, start: 0, end: 5 },
      { seg: 0, start: 16, end: 21 },
    ]);
    expect(findInSegments(segs, "cafe")).toEqual([{ seg: 1, start: 0, end: 4 }]);
    expect(findInSegments(segs, "c")).toEqual([]);
  });
  it("folds without changing length", () => {
    expect(fold("Café")).toBe("cafe");
    expect(fold("😀a").length).toBe("😀a".length);
  });
  it("splits text into runs around ranges", () => {
    expect(splitRuns("abcdef", [{ start: 1, end: 3, kind: "hit" }])).toEqual([
      { text: "a", kind: null },
      { text: "bc", kind: "hit" },
      { text: "def", kind: null },
    ]);
    // Overlaps keep the first range.
    expect(
      splitRuns("abcdef", [
        { start: 0, end: 3, kind: "x" },
        { start: 2, end: 4, kind: "y" },
      ]).map((r) => r.kind),
    ).toEqual(["x", null]);
  });
  it("marks entity names on word boundaries only", () => {
    expect(entityRanges("Meridian and Meridians met Meridian.", ["Meridian"])).toEqual([
      { start: 0, end: 8, kind: "entity" },
      { start: 27, end: 35, kind: "entity" },
    ]);
  });
});

describe("transcript corrections", () => {
  it("describes edits in plain words", () => {
    const name = (id: number | null) => (id === 7 ? "Host B" : "nobody");
    expect(
      describeEdit(
        {
          before: { text: "The card says Meridan brought" },
          after: { text: "The card says Meridian brought" },
        },
        name,
      ),
    ).toBe("Fixed “Meridan” → “Meridian”");
    expect(describeEdit({ before: { speaker: 3 }, after: { speaker: 7 } }, name)).toBe("Reassigned line to Host B");
    expect(describeEdit({ before: { speaker: 3 }, after: { speaker: null } }, name)).toBe("Unassigned the speaker");
    expect(describeEdit({ before: { text: "a b" }, after: { text: "a b c" } }, name)).toBe("Added “c”");
  });
  it("finds the changed words", () => {
    expect(wordDiff("one two three", "one 2 three")).toEqual({
      removed: "two",
      added: "2",
    });
  });
  it("reverts only what the edit changed", () => {
    expect(
      revertPatch({
        before: { text: "old", speaker: 3 },
        after: { text: "new" },
      }),
    ).toEqual({ text: "old" });
    expect(
      revertPatch({
        before: { text: "x", speaker: null },
        after: { speaker: 4 },
      }),
    ).toEqual({ speaker: null });
    expect(revertPatch({ before: {}, after: {} })).toBeNull();
  });
});

describe("summaries", () => {
  it("reads the Summarize step's shape", () => {
    const d = summaryDoc({
      summary: "They read the card.",
      topics: ["evals", "cyber"],
      action_items: ["Ask Meridian"],
      people: ["Meridian"],
      sentiment: "Neutral",
      importance: 3,
    });
    expect(d.tldr).toBe("They read the card.");
    expect(d.sections).toEqual([
      {
        key: "action_items",
        title: "Action items",
        glyph: "☐",
        items: [{ text: "Ask Meridian" }],
      },
    ]);
    expect(d.chips.map((c) => c.title)).toEqual(["Topics", "People mentioned"]);
    expect(d.facts).toEqual([
      { label: "Tone", value: "Neutral" },
      { label: "Importance", value: "3 of 5" },
    ]);
  });
  it("reads Meeting notes, in the design's section order", () => {
    const d = summaryDoc({
      open_questions: ["Would scores change?"],
      action_items: [{ owner: "Host B", task: "Ask Meridian", due: "Friday" }],
      decisions: ["Own episode"],
      tldr: "Short.",
    });
    expect(d.tldr).toBe("Short.");
    expect(d.sections.map((s) => [s.title, s.glyph])).toEqual([
      ["Decisions", "◆"],
      ["Action items", "☐"],
      ["Open questions", "?"],
    ]);
    expect(d.sections[1].items[0]).toEqual({
      text: "Ask Meridian",
      who: "Host B",
      due: "Friday",
      t: null,
    });
  });
  it("keeps timestamps on items and unknown keys as sections", () => {
    const d = summaryDoc({
      key_points: ["[14:18] Nine days on cyber", { point: "Suite not described", t: 877 }],
      risks: ["Scope creep"],
      mood: { host: "calm" },
    });
    expect(d.sections[0].items).toEqual([
      { text: "Nine days on cyber", t: 858_000 },
      { text: "Suite not described", who: null, due: null, t: 877_000 },
    ]);
    expect(d.sections.map((s) => s.title)).toEqual(["Key points", "Risks", "Mood"]);
  });
  it("handles strings, arrays and nothing", () => {
    expect(summaryDoc("Plain text.").tldr).toBe("Plain text.");
    expect(summaryDoc(["a", "b"]).sections[0].items).toHaveLength(2);
    expect(summaryDoc(null).empty).toBe(true);
    expect(summaryDoc({}).empty).toBe(true);
  });
  it("parses item times", () => {
    expect(parseTime("14:18")).toBe(858_000);
    expect(parseTime(90)).toBe(90_000);
    expect(parseTime(858_000)).toBe(858_000);
    expect(parseTime("soon")).toBeNull();
    expect(humanKey("open_questions")).toBe("Open questions");
  });
});

describe("recording chat", () => {
  it("builds an answer from the stream", () => {
    let a = newAnswer("What did they decide?");
    a = applyChatEvent(a, {
      event: "passages",
      data: JSON.stringify([{ n: 1, recording_id: 1, t0: 6795, speaker: "Bob", text: "Wow" }]),
    });
    a = applyChatEvent(a, {
      event: "token",
      data: JSON.stringify({ text: "They were surprised " }),
    });
    a = applyChatEvent(a, {
      event: "token",
      data: JSON.stringify({ text: "[1]." }),
    });
    a = applyChatEvent(a, {
      event: "done",
      data: JSON.stringify({ message: 9 }),
    });
    expect(a.status).toBe("done");
    expect(a.text).toBe("They were surprised [1].");
    expect(a.passages[0].t0).toBe(6795);
  });
  it("keeps what came before Stop, and knows it was saved", () => {
    let a = applyChatEvent(newAnswer("q"), { event: "token", data: JSON.stringify({ text: "Friday " }) });
    a = applyChatEvent(a, { event: "stopped", data: "{}" });
    a = applyChatEvent(a, { event: "done", data: JSON.stringify({ message: 4 }) });
    expect([a.status, a.text, a.saved]).toEqual(["stopped", "Friday ", true]);
  });
  it("keeps errors", () => {
    const a = applyChatEvent(
      applyChatEvent(newAnswer("q"), {
        event: "error",
        data: JSON.stringify({ message: "No provider" }),
      }),
      { event: "done", data: "{}" },
    );
    expect(a).toMatchObject({ status: "error", error: "No provider" });
  });
  it("splits citations", () => {
    expect(citeParts("A [1, 2] b [3]")).toEqual([
      { kind: "text", text: "A " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
      { kind: "text", text: " b " },
      { kind: "cite", n: 3 },
    ]);
  });
  it("recognises this recording's own conversation", () => {
    expect(isRecordingChat({ recordings: [4] }, 4)).toBe(true);
    expect(isRecordingChat({ recordings: [4, 5] }, 4)).toBe(false);
    expect(isRecordingChat({ recordings: [4], speakers: [1] }, 4)).toBe(false);
    expect(isRecordingChat({}, 4)).toBe(false);
  });
});

describe("skip silence", () => {
  const segs = [
    { t0: 2000, t1: 3000 },
    { t0: 3500, t1: 5000 },
    { t0: 9000, t1: 10000 },
  ];
  it("finds pauses longer than the threshold, including the lead-in", () => {
    expect(silenceGaps(segs)).toEqual([
      [0, 2000],
      [5000, 9000],
    ]);
    expect(silenceGaps(segs, 2500)).toEqual([[5000, 9000]]);
  });
  it("jumps to just before speech resumes", () => {
    const gaps = silenceGaps(segs);
    expect(skipTarget(gaps, 6000)).toBe(8750);
    expect(skipTarget(gaps, 5100)).toBeNull(); // still inside the lead-in after speech
    expect(skipTarget(gaps, 4000)).toBeNull();
  });
});

describe("fitTabs (priority+ panel tabs)", () => {
  const w: Record<string, number> = {
    summary: 80,
    speakers: 80,
    entities: 80,
    chat: 60,
    notes: 60,
    history: 70,
    metadata: 90,
    iiif: 50,
    details: 70,
  };
  const tabs = ["summary", "speakers", "entities", "chat", "notes", "history"];
  const extra = ["metadata", "iiif", "details"];
  const width = (v: string) => w[v];

  test("everything fits: only the extra tabs are in the menu", () => {
    expect(fitTabs(tabs, extra, width, 480, 34, "summary")).toEqual({
      shown: tabs,
      overflow: extra,
    });
  });

  test("narrow: trailing tabs move into the menu, ahead of the extra tabs", () => {
    const r = fitTabs(tabs, extra, width, 420, 34, "summary");
    expect(r.shown).toEqual(["summary", "speakers", "entities", "chat", "notes"]);
    expect(r.overflow).toEqual(["history", ...extra]);
  });

  test("an active tab that would overflow stays on the row", () => {
    const r = fitTabs(tabs, extra, width, 420, 34, "history");
    expect(r.shown).toEqual(["summary", "speakers", "entities", "chat", "history"]);
    expect(r.overflow).toEqual(["notes", ...extra]);
  });

  test("an active menu tab joins the end of the row, pushing others out if needed", () => {
    const r = fitTabs(tabs, extra, width, 480, 34, "details");
    expect(r.shown).toEqual(["summary", "speakers", "entities", "chat", "notes", "details"]);
    expect(r.overflow).toEqual(["history", "metadata", "iiif"]);
  });

  test("no extra tabs and everything fits: no menu", () => {
    expect(fitTabs(["a", "b"], [], () => 50, 100, 34, "a")).toEqual({
      shown: ["a", "b"],
      overflow: [],
    });
    expect(fitTabs(["a", "b", "c"], [], () => 50, 100, 34, "c")).toEqual({
      shown: ["c"],
      overflow: ["a", "b"],
    });
  });
});
