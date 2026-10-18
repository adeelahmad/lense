/** Reactions and comments on the timeline (docs/timeline.md, Reactions and comments): the palette, how a line of
 * several items sums its reactions, and how a reaction changes the list before the server answers. No React here. */

import type { Reaction } from "@/app/openapi-client/types.gen";

/** The palette, in the order the API lists reactions. */
export const EMOJIS = ["👍", "❤️", "🎉", "👀", "😂", "😮", "😢", "🙏"] as const;
export const EMOJI_LABEL: Record<string, string> = {
  "👍": "thumbs up",
  "❤️": "heart",
  "🎉": "party",
  "👀": "eyes",
  "😂": "laughing",
  "😮": "surprised",
  "😢": "sad",
  "🙏": "thanks",
};

/** The reactions after giving `emoji` (or taking it back), as the server will have them: counts move by one and
 * `mine` follows, in the palette's order. */
export function applyReaction(list: Reaction[], emoji: string, on: boolean): Reaction[] {
  const by = new Map(list.map((r) => [r.emoji, { ...r }]));
  const r = by.get(emoji) ?? { emoji, count: 0, mine: false };
  if (r.mine === on) return list;
  r.count = Math.max(0, r.count + (on ? 1 : -1));
  r.mine = on;
  if (r.count > 0) by.set(emoji, r);
  else by.delete(emoji);
  return EMOJIS.filter((e) => by.has(e)).map((e) => by.get(e) as Reaction);
}

/** One line's reactions, over its items: each emoji's count summed, `mine` when you gave it on any. */
export function sumReactions(lists: Reaction[][]): Reaction[] {
  const by = new Map<string, Reaction>();
  for (const list of lists)
    for (const r of list) {
      const s = by.get(r.emoji) ?? { emoji: r.emoji, count: 0, mine: false };
      s.count += r.count;
      s.mine = s.mine || r.mine;
      by.set(r.emoji, s);
    }
  return EMOJIS.filter((e) => by.has(e)).map((e) => by.get(e) as Reaction);
}

/** "3 comments", "1 comment", or "" for none. */
export function commentsLabel(n: number): string {
  if (!n) return "";
  return n === 1 ? "1 comment" : `${n} comments`;
}
