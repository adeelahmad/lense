import type { RecordingSummary, SearchHit } from "@/app/openapi-client/types.gen";
import { findText } from "@/components/search/query";

/** A link to a moment: the recording page, seeking to `ms` (as whole seconds, `?t=`). */
export function recordingHref(id: number, ms?: number | null): string {
  return ms && ms > 0 ? `/resources/${id}?t=${Math.floor(ms / 1000)}` : `/resources/${id}`;
}

/** A link from a search to an asset, carrying what was searched for (`?q=`) so the asset opens with it found. */
export function withFind(href: string, q?: string | null): string {
  const text = findText(q ?? "");
  return text ? `${href}${href.includes("?") ? "&" : "?"}q=${encodeURIComponent(text)}` : href;
}

/** True when a recording has media to play (imported transcripts have none); undefined when unknown. */
export function hasMedia(r: Pick<RecordingSummary, "source" | "media_kind"> | undefined): boolean | undefined {
  if (!r) return undefined;
  return r.source === "audio" || r.media_kind === "audio" || r.media_kind === "video";
}

/** A hit on a page of a document or an image: its text, an object seen there, or what the page shows. */
export function onPage(hit: Pick<SearchHit, "source" | "page">): boolean {
  return hit.source === "page" || ((hit.source === "object" || hit.source === "described") && hit.page != null);
}

/** Where a hit was found, when it wasn't said: on screen, on the page, seen, or what a shot or page shows. */
export function foundAs(hit: Pick<SearchHit, "source" | "page">): string | null {
  const paged = onPage(hit);
  if (hit.source === "screen") return "On screen";
  if (hit.source === "object") return paged ? "Seen on the page" : "Seen on screen";
  if (hit.source === "described") return paged ? "What the page shows" : "What the shot shows";
  if (hit.source === "page") return "On the page";
  return null;
}
