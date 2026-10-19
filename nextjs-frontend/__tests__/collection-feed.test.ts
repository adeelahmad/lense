import { feedStatus } from "@/components/collections/feed-model";

describe("a collection's podcast feed", () => {
  const now = Date.parse("2026-10-18T12:00:00Z");

  it("says how many episodes it lists and whether a podcast app reads it", () => {
    expect(feedStatus({ episodes: 3, max_items: 50, fetched_at: "2026-10-18T11:55:00Z" }, now)).toBe(
      "3 episodes · a podcast app read it 5 minutes ago",
    );
    expect(feedStatus({ episodes: 1, max_items: 50, fetched_at: null }, now)).toBe(
      "1 episode · no podcast app has read it yet",
    );
  });

  it("says why it's empty, and when it lists only the newest", () => {
    expect(feedStatus({ episodes: 0, max_items: 50 }, now)).toBe(
      "No episodes yet: assets with audio or video become episodes · no podcast app has read it yet",
    );
    expect(feedStatus({ episodes: 50, max_items: 50, fetched_at: null }, now)).toMatch(/^The newest 50 episodes/);
  });
});
