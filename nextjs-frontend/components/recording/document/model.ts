/**
 * Documents and images on the resource page: which page a moment of the text is on, the blocks of each page, what to
 * call a page ("p. 3", or the PDF's own "iv"), the page in the address (?page=3), zoom steps and the boxes to mark on
 * a page. Pure, tested in __tests__/document-model.test.ts.
 */
import {
  segmentAt,
  type Box,
  type FaceTrack,
  type FindHit,
  type PageInfo,
  type Segment,
} from "@/components/recording/model";

/** Zoom, as a share of the width that fits the page in the viewer. */
export const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

/** The page a moment of the text is on (its block's, or the last one's before it); 0 without text. */
export function pageAt(segments: Segment[], ms: number): number {
  const i = segmentAt(segments, ms);
  return segments[Math.max(0, i)]?.page ?? 0;
}

/** Each page's blocks of text, as indexes into the segments, in reading order. */
export function blocksByPage(segments: Segment[]): Map<number, number[]> {
  const out = new Map<number, number[]>();
  segments.forEach((s, i) => {
    const p = s.page ?? 0;
    const list = out.get(p);
    if (list) list.push(i);
    else out.set(p, [i]);
  });
  return out;
}

/** Where a page's text starts (its first block's time), or null for a page without text. */
export function pageStart(segments: Segment[], page: number): number | null {
  const s = segments.find((x) => (x.page ?? 0) === page);
  return s ? s.t0 : null;
}

/** A page's number for people: 1 for the first, or the PDF's own name for it. */
export function pageNumber(pages: PageInfo[], idx: number): string {
  return pages[idx]?.label || String(idx + 1);
}

/** "p. 3" (or "p. iv"): where a moment of a document's text is. */
export function pageRef(pages: PageInfo[], idx: number): string {
  return `p. ${pageNumber(pages, idx)}`;
}

/** The page asked for in the address (?page=3, counting from 1), from 0 and within the document; null when none is. */
export function parsePage(raw: string | null | undefined, count: number): number | null {
  if (!raw || !/^\d+$/.test(raw.trim())) return null;
  return clampPage(Number(raw) - 1, count);
}

export function clampPage(idx: number, count: number): number {
  return Math.max(0, Math.min(Math.max(0, count - 1), Math.round(idx)));
}

/** The next zoom in (dir 1) or out (-1); the fit (1) when it's in between. */
export function zoomStep(z: number, dir: 1 | -1): number {
  if (dir > 0) return ZOOMS.find((x) => x > z + 1e-6) ?? ZOOMS[ZOOMS.length - 1];
  return [...ZOOMS].reverse().find((x) => x < z - 1e-6) ?? ZOOMS[0];
}

export type Mark = { box: Box; kind: "selected" | "hit" | "current-hit" };

/** What to mark on a page: the block someone chose, and the blocks find matched (the current match apart). */
export function marksOn(
  segments: Segment[],
  page: number,
  selected: number | null,
  hits: FindHit[],
  hitIndex: number,
): Mark[] {
  const out: Mark[] = [];
  const current = hits[hitIndex]?.seg;
  const seen = new Set<number>();
  for (const h of hits) {
    const s = segments[h.seg];
    if (seen.has(h.seg) || !s?.box || (s.page ?? 0) !== page) continue;
    seen.add(h.seg);
    out.push({ box: s.box, kind: h.seg === current ? "current-hit" : "hit" });
  }
  const sel = selected != null ? segments[selected] : null;
  if (sel?.box && (sel.page ?? 0) === page && !seen.has(selected as number))
    out.push({ box: sel.box, kind: "selected" });
  return out;
}

/** How a page's text was read, for its caption: "Text from the PDF", "Read by OCR", "No text found". */
export function textNote(p: PageInfo | undefined): string {
  if (!p) return "";
  if (p.text === "ocr") return "Read by OCR";
  if (p.text === "pdf") return "Text from the PDF";
  return "No text found";
}

/** "12 pages · 3 read by OCR" for the header. */
export function pagesSummary(pages: PageInfo[], kind: "document" | "image"): string {
  const n = pages.length;
  const ocr = pages.filter((p) => p.text === "ocr").length;
  const head = kind === "image" && n <= 1 ? "Image" : `${n} page${n === 1 ? "" : "s"}`;
  return ocr && (kind === "document" || n > 1) ? `${head} · ${ocr} read by OCR` : head;
}

/** The pages a face is on, from its track's spans over pages ([from, to), counting from 0): "p. 1, 3–4". */
export function facePages(pages: PageInfo[], spans: [number, number][]): string {
  const parts = spans.map(([a, b]) =>
    b - a > 1 ? `${pageNumber(pages, a)}–${pageNumber(pages, b - 1)}` : pageNumber(pages, a),
  );
  return parts.length ? `p. ${parts.join(", ")}` : "";
}

export type FaceMark = { box: Box; track: number };

/** The faces on a page: each track's box there, with the track's place in the list (its colour and name). */
export function facesOn(faces: FaceTrack[], page: number): FaceMark[] {
  const out: FaceMark[] = [];
  faces.forEach((f, track) => {
    for (const [t, x, y, w, h] of f.boxes) if (t === page) out.push({ box: [x, y, w, h], track });
  });
  return out;
}

export type EmailInfo = {
  subject?: string | null;
  from?: string | null;
  to?: string | null;
  cc?: string | null;
  date?: string | null;
};

/** An email's own description as rows for the Details tab (only the ones it has). */
export function emailRows(e: EmailInfo | null | undefined, when: (iso: string) => string): [string, string][] {
  if (!e) return [];
  const rows: [string, string | null | undefined][] = [
    ["Subject", e.subject],
    ["From", e.from],
    ["To", e.to],
    ["Cc", e.cc],
    ["Sent", e.date ? when(e.date) : null],
  ];
  return rows.filter((r): r is [string, string] => Boolean(r[1]));
}

/** How a document that isn't a PDF was made into one: "made into a PDF by LibreOffice". */
export function renditionNote(r: { by?: string | null } | null | undefined): string | null {
  if (!r?.by) return null;
  return `made into a PDF by ${r.by === "libreoffice" ? "LibreOffice" : "Chromium"}`;
}

export type WebPageInfo = { url: string; final?: string | null; captured_at?: string | null; how?: string | null };

/** A captured web page as rows for the Details tab: where it was, where it ended up, when and how it was kept. */
export function webRows(w: WebPageInfo | null | undefined, when: (iso: string) => string): [string, string][] {
  if (!w) return [];
  const rows: [string, string | null | undefined][] = [
    ["Address", w.url],
    ["Ended at", w.final && w.final !== w.url ? w.final : null],
    ["Captured", w.captured_at ? when(w.captured_at) : "when its pipeline runs"],
    ["Kept as", w.how === "pdf" ? "the PDF it was" : w.how === "printed" ? "printed by the server’s browser" : null],
  ];
  return rows.filter((r): r is [string, string] => Boolean(r[1]));
}
