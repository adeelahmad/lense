/**
 * The recording page's data model: the loosely typed player payload (GET /recordings/{id}/player) parsed into strict
 * types, plus the pure helpers the page, the player and the transcript share (turns, lookups, lanes, ticks, find).
 * Everything here is free of React so it can be unit tested.
 */
import type { Player } from "@/app/openapi-client/types.gen";
import { speakerColor } from "@/components/ui/badge";
import { tc } from "@/lib/format";

export type MediaKind = "audio" | "video";
export type FacesMode = "off" | "detect" | "recognize";

export type SpeakerInfo = {
  /** "s12": the key segments use. */
  key: string;
  id: number;
  name: string;
  /** Order of first appearance, which picks the colour (spk-1..8). */
  index: number;
  color: string;
};

export type Segment = {
  /** Position in the transcript; the backend's segment index (used by PATCH /segments/{idx}). */
  idx: number;
  t0: number;
  t1: number;
  speaker: string | null;
  text: string;
  emotion: string | null;
  event: string | null;
  /** Timed words, when transcription gave them: [c0, c1, t0, t1], a character range of `text` and when it was said. */
  words?: Word[];
};

export type Word = [c0: number, c1: number, t0: number, t1: number];

export type Chapter = {
  idx: number;
  seg0: number;
  seg1: number;
  t0: number;
  t1: number;
  title: string;
};
export type EntityRef = { name: string; type: string; segs: number[] };
export type Keyword = { text: string; weight: number };
export type Shot = {
  idx: number;
  t0: number;
  t1: number;
  frame: string | null;
};
/** x, y, w, h as fractions of the frame. */
export type Box = [number, number, number, number];
export type ScreenText = {
  id: string;
  t0: number;
  t1: number;
  text: string;
  box: Box | null;
  frame: string | null;
  edited: boolean;
};
export type FaceTrack = {
  id: string;
  local: string;
  face: number | null;
  name: string;
  spans: [number, number][];
  screenMs: number;
  firstMs: number;
  /** [t, x, y, w, h] samples. */
  boxes: [number, number, number, number, number][];
  score: number | null;
  match: string | null;
  cover: string | null;
};

export type PlayerModel = {
  id: number;
  title: string;
  namespace: string | null;
  recordedAt: string | null;
  durationMs: number;
  audio: string | null;
  speakers: SpeakerInfo[];
  segments: Segment[];
  chapters: Chapter[];
  entities: EntityRef[];
  keywords: Keyword[];
  /** Loudness per time bin, 0..1; null when there's no audio analysis. */
  envelope: number[] | null;
  summary: Record<string, unknown> | null;
  media: {
    kind: MediaKind;
    width: number | null;
    height: number | null;
    fps: number | null;
  };
  shots: Shot[];
  screenText: ScreenText[];
  faces: FaceTrack[];
  facesMode: FacesMode;
  poster: string | null;
};

const num = (v: unknown, d = 0): number =>
  typeof v === "number" && Number.isFinite(v)
    ? v
    : typeof v === "string" && v.trim() && Number.isFinite(Number(v))
      ? Number(v)
      : d;
const str = (v: unknown): string | null => (typeof v === "string" ? v : v == null ? null : String(v));
const rec = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function box(v: unknown): Box | null {
  const a = arr(v).map((x) => num(x, NaN));
  return a.length >= 4 && a.slice(0, 4).every(Number.isFinite) ? [a[0], a[1], a[2], a[3]] : null;
}

/** The envelope arrives as bytes (0..255), fractions, or base64; anything else is ignored. */
export function normalizeEnvelope(v: unknown): number[] | null {
  let values: number[] = [];
  if (Array.isArray(v)) values = v.map((x) => num(x, 0));
  else if (typeof v === "string" && v.length) {
    try {
      const bin = typeof atob === "function" ? atob(v) : "";
      values = Array.from(bin, (c) => c.charCodeAt(0));
    } catch {
      return null;
    }
  }
  if (!values.length) return null;
  const max = Math.max(...values);
  if (!(max > 0)) return null;
  const scale = max > 1 ? 255 : 1;
  return values.map((x) => Math.max(0, Math.min(1, x / scale)));
}

const MODES: FacesMode[] = ["off", "detect", "recognize"];

/** Parses GET /recordings/{id}/player defensively: missing or odd fields become empty, never throw. */
export function normalizePlayer(raw: Player): PlayerModel {
  const r = raw as Record<string, unknown>;
  const speakers: SpeakerInfo[] = arr(r.speakers).map((s, i) => {
    const o = rec(s);
    const id = num(o.id, i + 1);
    return {
      key: str(o.key) ?? `s${id}`,
      id,
      name: str(o.name) ?? `Speaker ${id}`,
      index: i,
      color: speakerColor(i),
    };
  });
  const segments: Segment[] = arr(r.segments).map((s, i) => {
    const o = rec(s);
    const t0 = num(o.t0);
    return {
      idx: i,
      t0,
      t1: Math.max(t0, num(o.t1, t0)),
      speaker: str(o.s),
      text: str(o.text) ?? "",
      emotion: str(o.e),
      event: str(o.v),
      ...wordsOf(o.w, (str(o.text) ?? "").length),
    };
  });
  const lastEnd = segments.reduce((m, s) => Math.max(m, s.t1), 0);
  const media = rec(r.media);
  const kind: MediaKind = media.kind === "video" ? "video" : "audio";
  const mode = str(r.faces_mode) as FacesMode | null;
  return {
    id: num(r.id),
    title: str(r.title) || "Untitled recording",
    namespace: str(r.namespace),
    recordedAt: str(r.recorded_at),
    durationMs: Math.max(num(r.duration_ms), lastEnd),
    audio: str(r.audio),
    speakers,
    segments,
    chapters: arr(r.sections).map((s, i) => {
      const o = rec(s);
      return {
        idx: num(o.idx, i),
        seg0: num(o.seg0),
        seg1: num(o.seg1),
        t0: num(o.t0),
        t1: num(o.t1),
        title: str(o.title) || `Chapter ${i + 1}`,
      };
    }),
    entities: arr(r.entities).map((e) => {
      const o = rec(e);
      return {
        name: str(o.name) ?? "",
        type: str(o.type) ?? "TERM",
        segs: arr(o.segs)
          .map((x) => num(x, -1))
          .filter((x) => x >= 0),
      };
    }),
    keywords: arr(r.keywords)
      .map((k) =>
        Array.isArray(k)
          ? { text: str(k[0]) ?? "", weight: num(k[1], 1) }
          : {
              text: str(rec(k).text ?? k) ?? "",
              weight: num(rec(k).weight, 1),
            },
      )
      .filter((k) => k.text),
    envelope: normalizeEnvelope(r.envelope),
    summary: r.summary && typeof r.summary === "object" ? (r.summary as Record<string, unknown>) : null,
    media: {
      kind,
      width: num(media.width) || null,
      height: num(media.height) || null,
      fps: num(media.fps) || null,
    },
    shots: arr(r.shots).map((s, i) => {
      const o = rec(s);
      return {
        idx: num(o.idx, i),
        t0: num(o.t0),
        t1: num(o.t1),
        frame: str(o.frame),
      };
    }),
    screenText: arr(r.screen_text).map((s, i) => {
      const o = rec(s);
      return {
        id: str(o.id) ?? String(i),
        t0: num(o.t0),
        t1: num(o.t1),
        text: str(o.text) ?? "",
        box: box(o.box),
        frame: str(o.frame),
        edited: Boolean(o.edited),
      };
    }),
    faces: arr(r.faces).map((f, i) => {
      const o = rec(f);
      return {
        id: str(o.id) ?? String(i),
        local: str(o.local) ?? `P${i + 1}`,
        face: o.face == null ? null : num(o.face, 0) || null,
        name: str(o.name) ?? `Person ${i + 1}`,
        spans: arr(o.spans)
          .map((p) => arr(p).map((x) => num(x)))
          .filter((p) => p.length >= 2)
          .map((p) => [p[0], p[1]] as [number, number]),
        screenMs: num(o.screen_ms),
        firstMs: num(o.first_ms),
        boxes: arr(o.boxes)
          .map((b) => arr(b).map((x) => num(x)))
          .filter((b) => b.length >= 5)
          .map((b) => [b[0], b[1], b[2], b[3], b[4]] as [number, number, number, number, number]),
        score: o.score == null ? null : num(o.score),
        match: str(o.match),
        cover: str(o.cover),
      };
    }),
    facesMode: mode && MODES.includes(mode) ? mode : "off",
    poster: str(r.poster),
  };
}

// ---------- turns ----------

export type Turn = {
  /** Stable key: the first segment's index. */
  key: number;
  speaker: string | null;
  t0: number;
  t1: number;
  /** Segment indices, in order. */
  segs: number[];
};

/**
 * Consecutive segments by one speaker become a turn, so the transcript reads as a conversation. Long monologues are cut
 * every `maxSegs` segments or `maxMs` so timestamps stay frequent; segments with no speaker yet (still being
 * transcribed) stay one per turn.
 */
export function groupTurns(segments: Segment[], maxSegs = 6, maxMs = 90_000): Turn[] {
  const out: Turn[] = [];
  for (const s of segments) {
    const last = out[out.length - 1];
    if (last && s.speaker && last.speaker === s.speaker && last.segs.length < maxSegs && s.t1 - last.t0 <= maxMs) {
      last.segs.push(s.idx);
      last.t1 = Math.max(last.t1, s.t1);
    } else
      out.push({
        key: s.idx,
        speaker: s.speaker,
        t0: s.t0,
        t1: s.t1,
        segs: [s.idx],
      });
  }
  return out;
}

/** Index of the last item whose t0 is at or before `t` (-1 before the first). Items must be sorted by t0. */
export function indexAt<T extends { t0: number }>(items: T[], t: number): number {
  let lo = 0;
  let hi = items.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (items[mid].t0 <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** The segment being spoken at `t`, or the last one before it (so a pause keeps the line lit). */
/** A line's timed words from the API, kept only when they fit its text: `{ words }`, or nothing. */
function wordsOf(raw: unknown, len: number): { words?: Word[] } {
  const words = arr(raw).filter(
    (w): w is Word =>
      Array.isArray(w) &&
      w.length === 4 &&
      w.every((x) => typeof x === "number" && Number.isFinite(x)) &&
      w[0] >= 0 &&
      w[0] < w[1] &&
      w[1] <= len,
  );
  return words.length ? { words } : {};
}

/** The word being said at `ms`: the last one to have started (-1 before the first). */
export function wordAt(words: Word[], ms: number): number {
  let lo = 0;
  let hi = words.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid][2] <= ms) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** Scripts written without spaces between words (a line splits between any two characters there). */
const NO_SPACES = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]/;

/**
 * Where a line splits for a caret at `pos` (as the API does it): the start of the word the caret is in. Null when one
 * part would be empty. `rest` is the second part's start, to name it ("Split before “Today…”").
 */
export function splitPoint(text: string, pos: number): { at: number; rest: string } | null {
  let at = Math.max(0, Math.min(Math.round(pos), text.length));
  if (!NO_SPACES.test(text))
    while (at > 0 && at < text.length && !/\s/.test(text[at - 1]) && !/\s/.test(text[at])) at--;
  const head = text.slice(0, at).trim();
  const rest = text.slice(at).trim();
  return head && rest ? { at, rest } : null;
}

/** Where the second line's text starts once two lines are joined (as the API joins them: with a space, without one in
 * scripts written without spaces). */
export function joinedAt(a: string, b: string): number {
  const x = a.trimEnd();
  const y = b.trimStart();
  if (!x || !y) return x.length;
  return x.length + (NO_SPACES.test(x[x.length - 1]) || NO_SPACES.test(y[0]) ? 0 : 1);
}

/** A DOM range over characters c0..c1 of an element's text, or null when it doesn't have them. */
export function textRange(el: HTMLElement, c0: number, c1: number): Range | null {
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let at = 0;
  let started = false;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = n.textContent?.length ?? 0;
    if (!started && c0 < at + len) {
      range.setStart(n, c0 - at);
      started = true;
    }
    if (started && c1 <= at + len) {
      range.setEnd(n, c1 - at);
      return range;
    }
    at += len;
  }
  return null;
}

export function segmentAt(segments: Segment[], t: number): number {
  return indexAt(segments, t);
}

/** Turn containing a segment index. */
export function turnOf(turns: Turn[], seg: number): number {
  let lo = 0;
  let hi = turns.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (turns[mid].key <= seg) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}

/** Start of the previous (dir -1) or next (dir 1) turn from time `t`. Going back from inside a turn restarts it first. */
export function adjacentTurnStart(turns: Turn[], t: number, dir: -1 | 1): number | null {
  if (!turns.length) return null;
  const i = indexAt(turns, t);
  if (dir === 1) return i + 1 < turns.length ? turns[i + 1].t0 : null;
  if (i < 0) return null;
  // More than 1.5 s into a turn: back to its start; otherwise to the previous one.
  if (t - turns[i].t0 > 1500 || i === 0) return turns[i].t0;
  return turns[i - 1].t0;
}

export function chapterAt(chapters: Chapter[], t: number): number {
  return indexAt(chapters, t);
}

// ---------- timeline lanes ----------

/** Fractions of the recording (0..1) where each speaker talks, merged when closer than `mergeGap` of the length. */
export function speakerSpans(
  segments: Segment[],
  durationMs: number,
  mergeGap = 0.002,
): Map<string, [number, number][]> {
  const out = new Map<string, [number, number][]>();
  if (durationMs <= 0) return out;
  for (const s of segments) {
    if (!s.speaker) continue;
    const a = Math.max(0, s.t0 / durationMs);
    const b = Math.min(1, Math.max(s.t1, s.t0) / durationMs);
    const list = out.get(s.speaker) ?? [];
    const last = list[list.length - 1];
    if (last && a - last[1] <= mergeGap) last[1] = Math.max(last[1], b);
    else list.push([a, b]);
    out.set(s.speaker, list);
  }
  return out;
}

export type Bar = { h: number; played: boolean };

/**
 * Waveform bars per speaker lane: the loudness envelope sampled into `n` bars, each bar credited to whoever is
 * talking at that moment (other lanes get a flat stub). With no speaker, the bar goes to the "" lane.
 */
export function laneBars(
  envelope: number[] | null,
  segments: Segment[],
  durationMs: number,
  n: number,
  laneKeys: string[],
): Map<string, number[]> {
  const lanes = new Map<string, number[]>(laneKeys.map((k) => [k, new Array(n).fill(0)]));
  if (durationMs <= 0 || n <= 0) return lanes;
  for (let i = 0; i < n; i++) {
    const tMid = ((i + 0.5) / n) * durationMs;
    let h = 0.5;
    if (envelope && envelope.length) {
      const a = Math.floor((i / n) * envelope.length);
      const b = Math.max(a + 1, Math.floor(((i + 1) / n) * envelope.length));
      h = 0;
      for (let j = a; j < b && j < envelope.length; j++) h = Math.max(h, envelope[j]);
    }
    const si = segmentAt(segments, tMid);
    const seg = si >= 0 ? segments[si] : null;
    const inside = seg && tMid <= seg.t1 + 250;
    const key = inside ? (seg.speaker ?? "") : null;
    if (key !== null && lanes.has(key)) lanes.get(key)![i] = Math.max(0.06, h);
  }
  return lanes;
}

/** How much of the recording has been transcribed so far (for the processing waveform). */
export function transcribedFraction(segments: Segment[], durationMs: number): number {
  if (!segments.length || durationMs <= 0) return 0;
  return Math.min(1, segments[segments.length - 1].t1 / durationMs);
}

// ---------- labels ----------

/** "14:32 of 47:18, Host B, chapter 5" — the waveform slider's aria-valuetext. */
export function ariaTimeText(t: number, durationMs: number, speaker?: string | null, chapter?: number | null): string {
  const parts = [`${tc(t)} of ${tc(durationMs)}`];
  if (speaker) parts.push(speaker);
  if (chapter != null && chapter >= 0) parts.push(`chapter ${chapter + 1}`);
  return parts.join(", ");
}

/** Nice axis labels: a few round times plus the end, never crowding the end label. */
export function axisTicks(durationMs: number, maxTicks = 5): number[] {
  if (durationMs <= 0) return [0];
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200].map((s) => s * 1000);
  const step = steps.find((s) => durationMs / s <= maxTicks) ?? steps[steps.length - 1];
  const out: number[] = [];
  for (let t = 0; t < durationMs - step * 0.35; t += step) out.push(t);
  out.push(durationMs);
  return out;
}

// ---------- find ----------

export type FindHit = { seg: number; start: number; end: number };

/** Case- and accent-insensitive matches of `query` in the transcript, in reading order. */
export function findInSegments(segments: Segment[], query: string): FindHit[] {
  const q = fold(query.trim());
  if (q.length < 2) return [];
  const out: FindHit[] = [];
  for (const s of segments) {
    const text = fold(s.text);
    let from = 0;
    for (;;) {
      const at = text.indexOf(q, from);
      if (at < 0) break;
      out.push({ seg: s.idx, start: at, end: at + q.length });
      from = at + q.length;
    }
  }
  return out;
}

/** Lowercase and strip accents without changing the string's length (so offsets map back). */
export function fold(s: string): string {
  let out = "";
  for (const ch of s) {
    const base = ch.normalize("NFD").replace(/[̀-ͯ]/g, "");
    out += (base.length === ch.length ? base : ch).toLowerCase();
  }
  return out.length === s.length ? out : s.toLowerCase();
}

/** Split text into plain / highlighted runs for the given ranges (sorted, non-overlapping). */
export function splitRuns(
  text: string,
  ranges: { start: number; end: number; kind: string }[],
): { text: string; kind: string | null }[] {
  const out: { text: string; kind: string | null }[] = [];
  let at = 0;
  for (const r of [...ranges].sort((a, b) => a.start - b.start)) {
    if (r.start < at || r.end <= r.start) continue;
    if (r.start > at) out.push({ text: text.slice(at, r.start), kind: null });
    out.push({ text: text.slice(r.start, r.end), kind: r.kind });
    at = r.end;
  }
  if (at < text.length) out.push({ text: text.slice(at), kind: null });
  return out;
}

/** Where an entity's name appears in a segment's text (for the dotted underline). */
export function entityRanges(text: string, names: string[]): { start: number; end: number; kind: string }[] {
  const folded = fold(text);
  const out: { start: number; end: number; kind: string }[] = [];
  for (const name of names) {
    const q = fold(name.trim());
    if (q.length < 2) continue;
    let from = 0;
    for (;;) {
      const at = folded.indexOf(q, from);
      if (at < 0) break;
      const before = at === 0 ? " " : folded[at - 1];
      const after = folded[at + q.length] ?? " ";
      if (
        !/[\p{L}\p{N}]/u.test(before) &&
        !/[\p{L}\p{N}]/u.test(after) &&
        !out.some((r) => at < r.end && at + q.length > r.start)
      ) {
        out.push({ start: at, end: at + q.length, kind: "entity" });
      }
      from = at + q.length;
    }
  }
  return out.sort((a, b) => a.start - b.start);
}

// ---------- speakers ----------

export type SpeakerStat = {
  id: number | null;
  name: string;
  talkMs: number;
  turns: number;
  words: number;
  wpm: number;
  share: number;
};

/** Talk time per speaker from the recording's stats (GET /recordings/{id} → stats.speakers). */
export function speakerStats(stats: Record<string, unknown> | null | undefined): SpeakerStat[] {
  const list = arr(rec(stats).speakers).map((s) => {
    const o = rec(s);
    return {
      id: o.speaker_id == null ? null : num(o.speaker_id),
      name: str(o.name) ?? "Unattributed",
      talkMs: num(o.talk_ms),
      turns: num(o.turns),
      words: num(o.words),
      wpm: num(o.wpm),
      share: 0,
    };
  });
  const total = list.reduce((a, s) => a + s.talkMs, 0);
  return list.map((s) => ({ ...s, share: total ? s.talkMs / total : 0 }));
}

/** A title as the API stores it: whitespace collapsed to single spaces, trimmed. */
export function cleanTitle(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

/** Emotions worth a chip: the backend writes "Unknown" (or nothing) when it couldn't tell. */
export function showEmotion(e: string | null | undefined): e is string {
  return Boolean(e && e !== "Unknown" && e !== "unknown");
}

/** Seconds from a `?t=` value ("872", "872.5", "14:32", "1:02:03"); null when unreadable. */
export function parseStart(v: string | string[] | null | undefined): number | null {
  const s = (Array.isArray(v) ? v[0] : v)?.trim();
  if (!s) return null;
  if (/^\d+(\.\d+)?s?$/.test(s)) return Number(s.replace(/s$/, ""));
  if (/^\d+(:\d{1,2}){1,2}(\.\d+)?$/.test(s)) return s.split(":").reduce((a, p) => a * 60 + Number(p), 0);
  return null;
}
