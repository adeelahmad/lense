/**
 * Video page logic, free of React: what's on screen at a time (face boxes, text boxes, the shot), the zoomable
 * timeline window, lane blocks, and text on screen grouped by shot. All times are ms.
 */
import {
  indexAt,
  type Box,
  type FaceTrack,
  type ScreenText,
  type Segment,
  type Shot,
} from "@/components/recording/model";

/** "12:41.20": minutes, seconds and hundredths (frame-accurate readout). */
export function fineTime(ms: number): string {
  const t = Math.max(0, ms);
  const cs = Math.floor((t % 1000) / 10);
  const s = Math.floor(t / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = s % 60;
  const base = h
    ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`
    : `${m}:${String(x).padStart(2, "0")}`;
  return `${base}.${String(cs).padStart(2, "0")}`;
}

/** One frame, in ms (25 fps when the rate isn't known). */
export function frameMs(fps: number | null | undefined): number {
  return 1000 / (fps && fps > 1 ? fps : 25);
}

/** The shot showing at `t` (-1 before the first). */
export function shotAt(shots: Shot[], t: number): number {
  return indexAt(shots, t);
}

/** Start of the previous/next shot from `t`; going back from well inside a shot restarts it first. */
export function adjacentShot(shots: Shot[], t: number, dir: -1 | 1): number | null {
  if (!shots.length) return null;
  const i = shotAt(shots, t);
  if (dir === 1) return i + 1 < shots.length ? shots[i + 1].t0 : null;
  if (i < 0) return null;
  if (t - shots[i].t0 > 1000 || i === 0) return shots[i].t0;
  return shots[i - 1].t0;
}

/**
 * A face's box at `t`: the nearest sample, if it's within `tolerance` ms and the face is on screen then (inside one of
 * its spans). Samples are [t, x, y, w, h] sorted by t.
 */
export function faceBoxAt(track: FaceTrack, t: number, tolerance = 3000): Box | null {
  if (!track.spans.some(([a, b]) => t >= a && t <= b)) return null;
  const b = track.boxes;
  if (!b.length) return null;
  let lo = 0;
  let hi = b.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (b[mid][0] < t) lo = mid + 1;
    else hi = mid;
  }
  const cands = [b[lo], b[lo - 1]].filter(Boolean);
  const best = cands.reduce((x, y) => (Math.abs(y[0] - t) < Math.abs(x[0] - t) ? y : x));
  return Math.abs(best[0] - t) <= tolerance ? [best[1], best[2], best[3], best[4]] : null;
}

/** Text on screen visible at `t` (with a box to draw). */
export function textAt(spans: ScreenText[], t: number): ScreenText[] {
  return spans.filter((s) => s.box && t >= s.t0 && t < s.t1);
}

/** Clamp a zoomed window [start, end] of length duration/zoom around `center`. */
export function timelineWindow(durationMs: number, zoom: number, center: number): [number, number] {
  const d = Math.max(1, durationMs);
  const len = d / Math.max(1, zoom);
  let start = center - len / 2;
  start = Math.max(0, Math.min(start, d - len));
  return [start, start + len];
}

/** The zoom for a first view: long videos open on a ~15 minute window, the rest show everything. */
export function initialZoom(durationMs: number): number {
  const mins = durationMs / 60000;
  if (mins <= 60) return 1;
  let z = 1;
  while (mins / z > 15 && z < 64) z *= 2;
  return z;
}

/** Spans [t0, t1] → blocks [left%, width%] inside the window, clipped; tiny ones stay visible. */
export function blocks(spans: [number, number][], win: [number, number], minPct = 0.4): [number, number][] {
  const [a, b] = win;
  const len = Math.max(1, b - a);
  const out: [number, number][] = [];
  for (const [s, e] of spans) {
    if (e < a || s > b) continue;
    const l = ((Math.max(s, a) - a) / len) * 100;
    const r = ((Math.min(e, b) - a) / len) * 100;
    out.push([l, Math.max(minPct, r - l)]);
  }
  return out;
}

/** Each speaker's talking spans (ms), from the transcript. */
export function voiceSpans(segments: Segment[]): Map<string, [number, number][]> {
  const m = new Map<string, [number, number][]>();
  for (const s of segments) {
    if (!s.speaker) continue;
    const list = m.get(s.speaker) ?? [];
    const last = list[list.length - 1];
    if (last && s.t0 - last[1] < 1500) last[1] = Math.max(last[1], s.t1);
    else list.push([s.t0, s.t1]);
    m.set(s.speaker, list);
  }
  return m;
}

export type ShotGroup = {
  shot: Shot | null;
  index: number;
  lines: ScreenText[];
};

/** Text on screen grouped by the shot it first appears in, optionally filtered (case-insensitive). */
export function groupByShot(spans: ScreenText[], shots: Shot[], query = ""): ShotGroup[] {
  const q = query.trim().toLowerCase();
  const groups = new Map<number, ShotGroup>();
  for (const s of spans) {
    if (q && !s.text.toLowerCase().includes(q)) continue;
    const i = shotAt(shots, s.t0);
    const g = groups.get(i) ?? {
      shot: i >= 0 ? shots[i] : null,
      index: i,
      lines: [],
    };
    g.lines.push(s);
    groups.set(i, g);
  }
  return [...groups.values()].sort((a, b) => a.index - b.index);
}

/** Merge a video's face tracks into at most `max` lanes (the rest are counted, not drawn). */
export function faceLanes(faces: FaceTrack[], max = 8): { lanes: FaceTrack[]; hidden: number } {
  const sorted = [...faces].sort((a, b) => b.screenMs - a.screenMs);
  return {
    lanes: sorted.slice(0, max).sort((a, b) => a.firstMs - b.firstMs),
    hidden: Math.max(0, sorted.length - max),
  };
}

/** "41 m", "3 m 20 s", "45 s" on screen. */
export function screenTime(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return m >= 10 || !r ? `${m} m` : `${m} m ${r} s`;
}
