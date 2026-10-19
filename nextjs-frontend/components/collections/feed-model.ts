import type { CollectionFeed } from "@/app/openapi-client/types.gen";
import { count, plural, relative } from "@/lib/format";

/** "3 episodes · a podcast app read it 5 minutes ago", or why it lists nothing yet. */
export function feedStatus(f: Pick<CollectionFeed, "episodes" | "max_items" | "fetched_at">, now = Date.now()): string {
  const episodes =
    f.episodes === 0
      ? "No episodes yet: assets with audio or video become episodes"
      : f.episodes >= f.max_items
        ? `The newest ${count(f.max_items)} episodes`
        : plural(f.episodes, "episode");
  const read = f.fetched_at ? `a podcast app read it ${relative(f.fetched_at, now)}` : "no podcast app has read it yet";
  return `${episodes} · ${read}`;
}
