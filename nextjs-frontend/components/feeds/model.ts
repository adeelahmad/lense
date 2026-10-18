/** Published feeds as the web app shows them (backend: app/domain/syndication.py). */
import type { FeedItem, FeedPublish } from "@/app/openapi-client/types.gen";

export const KINDS = [
  { value: "rss", label: "RSS", hint: "Everything added to it: resources, episodes, timeline activities and text" },
  { value: "podcast", label: "Podcast", hint: "Items with audio, for podcast apps; episodes once their audio is made" },
] as const;
export type Kind = (typeof KINDS)[number]["value"];

export const kindLabel = (kind: string): string => KINDS.find((k) => k.value === kind)?.label ?? kind;

/** What an item is about, as a word. */
export const ITEM_KINDS: Record<string, string> = {
  asset: "Resource",
  episode: "Episode",
  activity: "Timeline",
  text: "Text",
};

/** Where an item leads inside Lens (its link is the public one). */
export function itemHref(it: FeedItem): string | null {
  if (it.recording) return it.kind === "episode" ? `/podcasts/${it.recording}` : `/resources/${it.recording}`;
  if (it.kind === "activity") return "/timeline";
  return it.link ?? null;
}

/** The request that puts a removed item back: the same resource, activity or text, so it lands under the same guid
 * (`recording/<id>`, `activity/<id>`, `text/<guid>`) and nothing is listed twice. */
export function republish(it: FeedItem): FeedPublish {
  if (it.recording) return { recordings: [it.recording] };
  if (it.kind === "activity") return { activities: [it.guid.replace(/^activity\//, "")] };
  return {
    texts: [{ title: it.title, summary: it.summary, link: it.link, guid: it.guid.replace(/^text\//, "") || null }],
  };
}

/** How many items a reader sees, said. */
export function itemsText(n: number): string {
  return n === 0 ? "Nothing in it yet" : n === 1 ? "1 item" : `${n} items`;
}
