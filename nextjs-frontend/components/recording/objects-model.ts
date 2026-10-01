import { facePages } from "@/components/recording/document/model";
import type { Box, ObjectTrack, PageInfo } from "@/components/recording/model";
import { screenTime } from "@/components/recording/video/model";
import { plural, tc } from "@/lib/format";

/** "Person", "Cell phone": a kind of object as a name. */
export function objectName(label: string): string {
  const t = label.trim();
  return t ? t[0].toUpperCase() + t.slice(1) : "Object";
}

/** Where a kind of object is seen: "0:03–0:06, 1:10–1:20 and 2 more" in a video, "p. 1–2, 4" on pages. */
export function whereSeen(t: ObjectTrack, pages: PageInfo[], max = 3): string {
  const rest = t.spans.length - max;
  const more = rest > 0 ? ` and ${rest} more` : "";
  if (t.paged) return facePages(pages, t.spans.slice(0, max)) + more;
  return t.spans
    .slice(0, max)
    .map(([a, b]) => `${tc(a)}–${tc(b)}`)
    .join(", ")
    .concat(more);
}

/** How much of it there is: "on 2 pages" or "12 s on screen", and how often it was found. */
export function howMuch(t: ObjectTrack): string {
  const where = t.paged ? `on ${plural(t.screenMs, "page")}` : `${screenTime(t.screenMs)} on screen`;
  return `${where} · found ${plural(t.count, "time")}`;
}

/** Its boxes on a page of a document or an image. */
export function boxesOnPage(t: ObjectTrack, page: number): Box[] {
  return t.boxes.filter((b) => b[0] === page).map((b) => [b[1], b[2], b[3], b[4]]);
}

/**
 * Its boxes at `ms` in a video: those of the sampled frame nearest to it (within `tolerance`), while it's seen.
 * Several of a kind can be on one frame (three people), each with its box.
 */
export function boxesAt(t: ObjectTrack, ms: number, tolerance = 3000): Box[] {
  if (!t.boxes.length || !t.spans.some(([a, b]) => ms >= a && ms <= b)) return [];
  let near = t.boxes[0][0];
  for (const b of t.boxes) if (Math.abs(b[0] - ms) < Math.abs(near - ms)) near = b[0];
  if (Math.abs(near - ms) > tolerance) return [];
  return t.boxes.filter((b) => b[0] === near).map((b) => [b[1], b[2], b[3], b[4]]);
}
