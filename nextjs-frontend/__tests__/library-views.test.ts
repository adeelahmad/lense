import type { SavedView } from "@/app/openapi-client/types.gen";
import { NO_FILTERS } from "@/components/library/model";
import {
  describeView,
  fromView,
  groupViews,
  isShowing,
  sameState,
  viewMeta,
  viewState,
  whyNoShare,
} from "@/components/library/views-model";

const library = {
  filters: {
    ...NO_FILTERS,
    q: " capsid ",
    statuses: ["analyzed" as const, "failed" as const],
    speaker: { name: "Alice", ids: [3, 9] },
    date: "30d" as const,
    tags: ["Interview"],
  },
  view: "attention" as const,
  sort: { key: "title" as const, dir: "asc" as const },
};

const view = (over: Partial<SavedView>): SavedView => ({
  id: 1,
  name: "Capsid",
  namespace: "podcasts",
  shared: false,
  state: viewState(library),
  created_by: "vi@lens.test",
  mine: true,
  can_delete: true,
  ...over,
});

describe("saved views", () => {
  it("keeps the tab, filters (the speaker by name) and sort", () => {
    expect(viewState(library)).toEqual({
      tab: "attention",
      q: "capsid",
      statuses: ["analyzed", "failed"],
      speaker: "Alice",
      date: "30d",
      duration: "any",
      media: "any",
      tags: ["Interview"],
      sort: "title",
    });
    expect(viewState({ filters: NO_FILTERS, view: "all", sort: { key: "date", dir: "desc" } }).sort).toBe("-date");
  });

  it("brings them back, the speaker to be found by name", () => {
    const back = fromView(viewState(library));
    expect(back.view).toBe("attention");
    expect(back.sort).toEqual({ key: "title", dir: "asc" });
    expect(back.speaker).toBe("Alice");
    expect(back.filters).toEqual({ ...library.filters, q: "capsid", speaker: null });
    expect(fromView({ sort: "-duration" }).sort).toEqual({ key: "duration", dir: "desc" });
    expect(fromView(undefined)).toEqual({
      filters: NO_FILTERS,
      view: "all",
      sort: { key: "date", dir: "desc" },
      speaker: null,
    });
  });

  it("knows when the Library shows one", () => {
    const now = viewState(library);
    expect(sameState(now, { ...now, statuses: ["failed", "analyzed"], tags: ["interview"] })).toBe(true);
    expect(sameState(now, { ...now, sort: "-title" })).toBe(false);
    expect(sameState(now, { ...now, speaker: null })).toBe(false);
    expect(sameState({}, { tab: "all", sort: "-date", q: "  " })).toBe(true);
    expect(isShowing(view({}), now, "podcasts")).toBe(true);
    expect(isShowing(view({}), now, null)).toBe(false);
    expect(isShowing(view({ namespace: null }), now, null)).toBe(true);
  });

  it("describes them", () => {
    expect(describeView(viewState(library))).toBe(
      "Needs attention · “capsid” · Analyzed, Job failed · Alice · Last 30 days · #Interview · by title",
    );
    expect(describeView({})).toBe("All recordings");
    expect(describeView({ sort: "date" })).toBe("All recordings · by date, oldest first");
    expect(describeView({ media: "video", duration: "long" })).toBe("All recordings · 30–60 min · Video");
    expect(viewMeta(view({}))).toBe("podcasts");
    expect(viewMeta(view({ shared: true }))).toBe("podcasts · shared");
    expect(viewMeta(view({ namespace: null }))).toBe("All namespaces");
    expect(viewMeta(view({ mine: false, shared: true, created_by: "ed@lens.test" }))).toBe("by ed@lens.test");
  });

  it("lists yours first, then the shared ones by namespace", () => {
    const groups = groupViews([
      view({ id: 1, name: "Mine" }),
      view({ id: 2, name: "Team", mine: false, shared: true }),
      view({ id: 3, name: "Calls", mine: false, shared: true, namespace: "customer-calls" }),
      view({ id: 4, name: "Also mine", namespace: null }),
    ]);
    expect(groups.map((g) => [g.title, g.views.map((v) => v.name)])).toEqual([
      ["Your views", ["Mine", "Also mine"]],
      ["Shared in podcasts", ["Team"]],
      ["Shared in customer-calls", ["Calls"]],
    ]);
    expect(groupViews([])).toEqual([]);
    expect(groupViews(undefined)).toEqual([]);
  });

  it("says why a view can't be shared", () => {
    expect(whyNoShare(null, true, "Editors of podcasts can do this")).toMatch(/Pick a namespace/);
    expect(whyNoShare("podcasts", false, "Editors of podcasts can do this")).toBe("Editors of podcasts can do this");
    expect(whyNoShare("podcasts", true, "Editors of podcasts can do this")).toBeNull();
  });
});
